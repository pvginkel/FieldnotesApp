import { spawn } from 'node:child_process';
import { access, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

async function globalSetup() {
  // External services mode has been removed. All tests now use per-worker managed services.
  if (process.env.PLAYWRIGHT_MANAGED_SERVICES === 'false') {
    throw new Error(
      'PLAYWRIGHT_MANAGED_SERVICES=false is no longer supported. ' +
      'The external services mode has been removed. ' +
      'Please remove this environment variable. ' +
      'All tests now use per-worker managed services (backend, SSE gateway, and frontend) for isolation.'
    );
  }

  console.log('🔧 Setting up Playwright tests...');
  console.log('Service management: Per-worker (Playwright managed)');

  const seededDbPath = await initializeSeedDatabase();
  if (seededDbPath) {
    process.env.PLAYWRIGHT_SEEDED_SQLITE_DB = seededDbPath;
    console.log(`🗃️  Seeded Playwright SQLite database: ${seededDbPath}`);
  }
  console.log('⏭️  Worker fixtures will boot backend, SSE gateway, and frontend on demand');
}

export default globalSetup;

async function initializeSeedDatabase(): Promise<string | null> {
  const repoRoot = getRepoRoot();
  const backendRoot = resolve(repoRoot, process.env.BACKEND_ROOT || '../backend');
  const scriptPath = resolve(backendRoot, 'scripts/initialize-sqlite-database.sh');

  // Apps without a database (use_database=false) won't have this script.
  const scriptExists = await access(scriptPath).then(() => true, () => false);

  if (!scriptExists) {
    console.log('ℹ️  Database initialization script not found; skipping SQLite database seeding.');
    return null;
  }

  const tmpRoot = await mkdtemp(join(tmpdir(), 'pw-seed-'));
  const dbPath = join(tmpRoot, 'seed.sqlite');

  await runScript(scriptPath, ['--db', dbPath, '--load-test-data'], {
    cwd: repoRoot,
  });

  return dbPath;
}

async function runScript(
  command: string,
  args: readonly string[],
  options: { cwd: string }
): Promise<void> {
  await new Promise<void>((resolvePromise, rejectPromise) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        // Same override the per-worker backends get in process/servers.ts:
        // without it, OIDC_ENABLED=true from a developer's .env sends the
        // seed script off to a Keycloak that tests never need.
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
        console.error(`initialize-sqlite-database.sh failed output:\n${output}`);
      }
      rejectPromise(
        new Error(
          `${command} exited with code ${code ?? 'null'} while initializing Playwright database`
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
