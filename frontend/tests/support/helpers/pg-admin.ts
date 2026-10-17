/**
 * The PostgreSQL databases a Playwright run works on.
 *
 * Global setup builds a seed database with the backend's `load-test-data`,
 * each worker's backend serves a clone of it, and global teardown drops them
 * all. They live on the server the backend's pytest suite uses
 * (`TEST_DATABASE_ADMIN_URL`, the environment's postgres sidecar by default),
 * named `<prefix><run id>_<name>`. pytest's databases have a prefix of their
 * own (`<database_name>_test_`), so neither suite's sweep touches the other's.
 */

import { randomBytes } from 'node:crypto';
import { Client } from 'pg';
import { PROJECT_NAME } from '@/lib/consts';

// Keep in step with the backend's tests/conftest_infrastructure.py.
const DEFAULT_ADMIN_URL = 'postgresql+psycopg://postgres:postgres@localhost:5432/postgres';

const DATABASE_PREFIX = `${PROJECT_NAME.toLowerCase().replace(/-/g, '_')}_e2e_`;

// Production's locale: the clusters are initialised with ICU 'und' and C. The
// server's default (libc en_US.utf8 on the sidecar) sorts text differently.
const DATABASE_LOCALE =
  "ENCODING 'UTF8' LOCALE_PROVIDER icu ICU_LOCALE 'und' LC_COLLATE 'C' LC_CTYPE 'C'";

// PostgreSQL's SQLSTATE for a database that still has connections.
const OBJECT_IN_USE = '55006';

/** Global setup publishes the run's prefix to the workers and teardown here. */
export const RUN_PREFIX_VARIABLE = 'PLAYWRIGHT_DATABASE_PREFIX';

// The run's own connection, held from openRun() to closeRun().
let runConnection: Client | undefined;

/** A database's URL in SQLAlchemy's form, for the backend's DATABASE_URL. */
export function databaseUrl(name: string): string {
  const url = adminUrl();
  url.pathname = `/${name}`;
  return url.href;
}

/**
 * Start a run: drop what dead runs left behind, and create the seed database,
 * empty. Returns the run's prefix and the seed's name.
 *
 * The run holds one connection to the server for as long as it lasts, with
 * its prefix as the connection's application_name. That is how a run tells
 * another live run's databases, which it must leave alone, from the ones a
 * run that died left behind.
 */
export async function openRun(): Promise<{ prefix: string; seed: string }> {
  const prefix = `${DATABASE_PREFIX}${randomBytes(4).toString('hex')}`;
  runConnection = await connect(prefix);
  await sweep(runConnection);
  const seed = `${prefix}_seed`;
  await runConnection.query(`CREATE DATABASE "${seed}" TEMPLATE template0 ${DATABASE_LOCALE}`);
  return { prefix, seed };
}

/**
 * End a run: drop its databases, connections and all, and its connection.
 * Best effort: a failure is logged, and the next run's sweep drops what is
 * left.
 */
export async function closeRun(prefix: string): Promise<void> {
  const client = runConnection ?? (await connect());
  runConnection = undefined;
  try {
    for (const name of await databaseNames(client, `${prefix}_`)) {
      try {
        await client.query(`DROP DATABASE IF EXISTS "${name}" WITH (FORCE)`);
      } catch (error) {
        console.warn(`Failed to drop test database ${name}: ${errorMessage(error)}`);
      }
    }
  } finally {
    await client.end();
  }
}

/** Create a worker's database from the run's seed, and return its name. */
export async function cloneSeed(prefix: string, parallelIndex: number): Promise<string> {
  const name = `${prefix}_w${parallelIndex}`;
  const client = await connect();
  try {
    // A worker that replaces a failed one gets its parallel index, and may
    // find its predecessor's database.
    await client.query(`DROP DATABASE IF EXISTS "${name}" WITH (FORCE)`);
    await client.query(`CREATE DATABASE "${name}" TEMPLATE "${prefix}_seed"`);
  } finally {
    await client.end();
  }
  return name;
}

/** Drop one of the run's databases, connections and all. */
export async function dropDatabase(name: string): Promise<void> {
  const client = await connect();
  try {
    await client.query(`DROP DATABASE IF EXISTS "${name}" WITH (FORCE)`);
  } finally {
    await client.end();
  }
}

/**
 * Drop the databases that runs which are no longer alive left behind.
 *
 * A database that still has connections is skipped. The drop is without
 * FORCE, so one that gains a connection meanwhile refuses with ObjectInUse,
 * and is skipped too (after the server's 5 s wait).
 */
async function sweep(client: Client): Promise<void> {
  const liveRuns = new Set(
    (
      await client.query<{ application_name: string }>(
        'SELECT application_name FROM pg_stat_activity WHERE starts_with(application_name, $1)',
        [DATABASE_PREFIX]
      )
    ).rows.map(row => row.application_name)
  );
  const inUse = new Set(
    (
      await client.query<{ datname: string }>(
        'SELECT datname FROM pg_stat_activity WHERE starts_with(datname, $1)',
        [DATABASE_PREFIX]
      )
    ).rows.map(row => row.datname)
  );
  const runPrefixLength = DATABASE_PREFIX.length + 8;

  for (const name of await databaseNames(client, DATABASE_PREFIX)) {
    if (liveRuns.has(name.slice(0, runPrefixLength)) || inUse.has(name)) {
      continue;
    }
    try {
      await client.query(`DROP DATABASE IF EXISTS "${name}"`);
    } catch (error) {
      if ((error as { code?: string }).code !== OBJECT_IN_USE) {
        throw error;
      }
    }
  }
}

async function databaseNames(client: Client, prefix: string): Promise<string[]> {
  const result = await client.query<{ datname: string }>(
    'SELECT datname FROM pg_database WHERE starts_with(datname, $1)',
    [prefix]
  );
  return result.rows.map(row => row.datname);
}

async function connect(applicationName?: string): Promise<Client> {
  // pg takes postgresql://, where SQLAlchemy's URL names its driver too.
  const connectionString = adminUrl().href.replace(/^postgresql\+\w+:/, 'postgresql:');
  const client = new Client({ connectionString, application_name: applicationName });
  await client.connect();
  return client;
}

function adminUrl(): URL {
  return new URL(process.env.TEST_DATABASE_ADMIN_URL || DEFAULT_ADMIN_URL);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
