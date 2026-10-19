import { spawn } from 'node:child_process';
import { access } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { databaseUrl, openRun, RUN_PREFIX_VARIABLE } from './helpers/pg-admin';

async function globalSetup() {
  // External services mode has been removed. All tests now use per-worker managed services.
  if (process.env.PLAYWRIGHT_MANAGED_SERVICES === 'false') {
    throw new Error(
      'PLAYWRIGHT_MANAGED_SERVICES=false is no longer supported. ' +
      'The external services mode has been removed. ' +
      'Please remove this environment variable. ' +
      'All tests now use per-worker managed services (backend and frontend) for isolation.'
    );
  }

  console.log('🔧 Setting up Playwright tests...');
  console.log('Service management: Per-worker (Playwright managed)');

  await initializeSeedDatabase();
  console.log('⏭️  Worker fixtures will boot backend and frontend on demand');
}

export default globalSetup;

/**
 * Create the run's seed database and load the backend's test data into it.
 * Each worker's backend serves a clone of it (fixtures-infrastructure.ts),
 * and global teardown drops them all.
 */
async function initializeSeedDatabase(): Promise<void> {
  const repoRoot = getRepoRoot();
  const backendRoot = resolve(repoRoot, process.env.BACKEND_ROOT || '../backend');

  // The backend template renders alembic.ini only with use_database.
  const hasDatabase = await access(resolve(backendRoot, 'alembic.ini')).then(
    () => true,
    () => false
  );
  if (!hasDatabase) {
    console.log('ℹ️  The backend has no database; skipping database seeding.');
    return;
  }

  const { prefix, seed } = await openRun();
  // Workers clone the seed. Global teardown drops the run's databases, after
  // a failed setup too.
  process.env[RUN_PREFIX_VARIABLE] = prefix;

  // load-test-data recreates the database with the revisions, then loads the
  // app's test data.
  await runScript('uv', ['run', 'cli', 'load-test-data', '--yes-i-am-sure'], {
    cwd: backendRoot,
    env: { DATABASE_URL: databaseUrl(seed) },
  });

  console.log(`🗃️  Seeded Playwright database: ${seed}`);
}

async function runScript(
  command: string,
  args: readonly string[],
  options: { cwd: string; env: Record<string, string> }
): Promise<void> {
  await new Promise<void>((resolvePromise, rejectPromise) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        ...options.env,
        // Same override the per-worker backends get in process/servers.ts:
        // without it, OIDC_ENABLED=true from a developer's .env sends the
        // seed off to a Keycloak that tests never need.
        OIDC_ENABLED: 'false',
      },
    });

    let stdout = '';
    let stderr = '';

    if (child.stdout) {
      child.stdout.on('data', chunk => {
        stdout += chunk.toString();
      });
    }
    if (child.stderr) {
      child.stderr.on('data', chunk => {
        stderr += chunk.toString();
      });
    }

    child.on('error', rejectPromise);
    child.on('exit', code => {
      if (code === 0) {
        resolvePromise();
        return;
      }
      const output = [stdout.trim(), stderr.trim()].filter(Boolean).join('\n');
      if (output) {
        console.error(`${command} ${args.join(' ')} failed output:\n${output}`);
      }
      rejectPromise(
        new Error(
          `${command} ${args.join(' ')} exited with code ${code ?? 'null'} while seeding the Playwright database`
        )
      );
    });
  });
}

let repoRootCache: string | undefined;

function getRepoRoot(): string {
  if (!repoRootCache) {
    const currentDir = dirname(fileURLToPath(import.meta.url));
    repoRootCache = resolve(currentDir, '../..');
  }
  return repoRootCache;
}
