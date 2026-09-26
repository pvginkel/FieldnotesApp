import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import getPort from 'get-port';
import type { Readable } from 'node:stream';
import split2 from 'split2';

const BACKEND_READY_PATH = '/health/readyz';
const FRONTEND_READY_PATH = '/';
const GATEWAY_READY_PATH = '/readyz';
const BACKEND_STARTUP_TIMEOUT_MS = 30_000;
const FRONTEND_STARTUP_TIMEOUT_MS = 90_000;
const GATEWAY_STARTUP_TIMEOUT_MS = 30_000;
const POLL_INTERVAL_MS = 200;
const HOSTNAME = '127.0.0.1';

type ServerHandle = {
  readonly url: string;
  readonly port: number;
  readonly process: ChildProcessWithoutNullStreams;
  dispose(): Promise<void>;
};

export type BackendServerHandle = ServerHandle;
export type FrontendServerHandle = ServerHandle;
export type SSEGatewayServerHandle = ServerHandle;

export async function startBackend(
  workerIndex: number,
  options: {
    sqliteDbPath?: string;
    streamLogs?: boolean;
    port?: number;
    excludePorts?: number[];
    frontendVersionUrl?: string;
  }
): Promise<BackendServerHandle> {
  const port =
    typeof options.port === 'number'
      ? options.port
      : await getPort({
          exclude: options.excludePorts ?? [],
        });

  const scriptPath = resolve(getBackendRepoRoot(), './scripts/testing-server.sh');
  const args = [
    '--host',
    HOSTNAME,
    '--port',
    String(port),
    ...(options.sqliteDbPath ? ['--sqlite-db', options.sqliteDbPath] : []),
  ];

  return startService({
    workerIndex,
    port,
    serviceLabel: 'backend',
    scriptPath,
    args,
    readinessPath: BACKEND_READY_PATH,
    startupTimeoutMs: BACKEND_STARTUP_TIMEOUT_MS,
    streamLogs: options.streamLogs === true,
    env: {
      ...process.env,
      // Disable real OIDC in tests — the testing service endpoints
      // (/api/testing/auth/*) provide auth simulation regardless of this flag.
      // Without this override, OIDC_ENABLED=true from .env causes the
      // before_request hook to attempt real token validation → 401.
      OIDC_ENABLED: 'false',
      ...(options.frontendVersionUrl
        ? { FRONTEND_VERSION_URL: options.frontendVersionUrl }
        : {}),
    },
  });
}

export async function startFrontend(options: {
  workerIndex: number;
  backendUrl: string;
  excludePorts?: number[];
  streamLogs?: boolean;
  port?: number;
}): Promise<FrontendServerHandle> {
  const port =
    typeof options.port === 'number'
      ? options.port
      : await getPort({
          exclude: options.excludePorts ?? [],
        });

  const scriptPath = resolve(getFrontendRepoRoot(), './scripts/testing-server.sh');
  const args = ['--host', HOSTNAME, '--port', String(port)];

  return startService({
    workerIndex: options.workerIndex,
    port,
    serviceLabel: 'frontend',
    scriptPath,
    args,
    readinessPath: FRONTEND_READY_PATH,
    startupTimeoutMs: FRONTEND_STARTUP_TIMEOUT_MS,
    streamLogs: options.streamLogs === true,
    env: {
      ...process.env,
      BACKEND_URL: options.backendUrl,
      VITE_TEST_MODE: 'true',
    },
  });
}

export async function startSSEGateway(options: {
  workerIndex: number;
  backendUrl: string;
  excludePorts?: number[];
  streamLogs?: boolean;
  port?: number;
}): Promise<SSEGatewayServerHandle> {
  const port =
    typeof options.port === 'number'
      ? options.port
      : await getPort({
          exclude: options.excludePorts ?? [],
        });

  const require = createRequire(import.meta.url);
  const gatewayEntry = require.resolve('ssegateway');
  const callbackUrl = `${options.backendUrl}/api/sse/callback`;

  return startService({
    workerIndex: options.workerIndex,
    port,
    serviceLabel: 'sse-gateway',
    scriptPath: process.execPath,
    args: [gatewayEntry],
    readinessPath: GATEWAY_READY_PATH,
    startupTimeoutMs: GATEWAY_STARTUP_TIMEOUT_MS,
    streamLogs: options.streamLogs === true,
    env: {
      ...process.env,
      PORT: String(port),
      CALLBACK_URL: callbackUrl,
    },
  });
}

type ServiceLabel = 'backend' | 'frontend' | 'sse-gateway';

type StartServiceOptions = {
  workerIndex: number;
  port: number;
  serviceLabel: ServiceLabel;
  scriptPath: string;
  args: string[];
  env: NodeJS.ProcessEnv;
  readinessPath: string;
  startupTimeoutMs: number;
  streamLogs: boolean;
};

async function startService(options: StartServiceOptions): Promise<ServerHandle> {
  const url = `http://${HOSTNAME}:${options.port}`;
  const readinessUrl = buildReadinessUrl(url, options.readinessPath);

  const logLifecycle = options.streamLogs;
  if (logLifecycle) {
    console.log(
      `${formatPrefix(options.workerIndex, options.serviceLabel)} Starting ${options.serviceLabel}: ${options.scriptPath} ${options.args.join(' ')}`
    );
  }

  const child = spawn(options.scriptPath, options.args, {
    cwd: getFrontendRepoRoot(),
    env: options.env,
    stdio: ['pipe', 'pipe', 'pipe'],
  }) as ChildProcessWithoutNullStreams;

  registerForCleanup(child);

  if (options.streamLogs) {
    streamProcessOutput(child, options.workerIndex, options.serviceLabel);
  }

  await waitForStartup({
    workerIndex: options.workerIndex,
    process: child,
    readinessUrl,
    serviceLabel: options.serviceLabel,
    startupTimeoutMs: options.startupTimeoutMs,
  });

  if (logLifecycle) {
    console.log(
      `${formatPrefix(options.workerIndex, options.serviceLabel)} ${capitalize(options.serviceLabel)} ready at ${url}`
    );
  }

  return {
    url,
    port: options.port,
    process: child,
    dispose: () => terminateProcess(child, options.workerIndex, options.serviceLabel),
  };
}

function buildReadinessUrl(baseUrl: string, path: string): string {
  return new URL(path, ensureTrailingSlash(baseUrl)).toString();
}

function ensureTrailingSlash(url: string): string {
  return url.endsWith('/') ? url : `${url}/`;
}

async function waitForStartup({
  workerIndex,
  process,
  readinessUrl,
  serviceLabel,
  startupTimeoutMs,
}: {
  workerIndex: number;
  process: ChildProcessWithoutNullStreams;
  readinessUrl: string;
  serviceLabel: ServiceLabel;
  startupTimeoutMs: number;
}) {
  const start = performance.now();

  const exitPromise = new Promise<never>((_, reject) => {
    process.once('exit', (code, signal) => {
      reject(
        new Error(
          `${formatPrefix(workerIndex, serviceLabel)} process exited before ready (code=${code}, signal=${signal})`
        )
      );
    });
    process.once('error', error => {
      reject(
        new Error(
          `${formatPrefix(workerIndex, serviceLabel)} process failed to start: ${error instanceof Error ? error.message : String(error)}`
        )
      );
    });
  });

  const poll = async () => {
    while (performance.now() - start < startupTimeoutMs) {
      try {
        const response = await fetch(readinessUrl, {
          method: 'GET',
          cache: 'no-store',
        });
        if (response.ok) {
          return;
        }
      } catch {
        // Service not ready yet; retry until timeout.
      }

      await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS));
    }

    throw new Error(
      `${formatPrefix(workerIndex, serviceLabel)} Timed out waiting for readiness at ${readinessUrl}`
    );
  };

  await Promise.race([exitPromise, poll()]);
}

async function terminateProcess(
  child: ChildProcessWithoutNullStreams,
  workerIndex: number,
  serviceLabel: ServiceLabel
) {
  if (child.exitCode !== null || child.signalCode) {
    return;
  }

  const logLifecycle = serviceShouldLogLifecycle(serviceLabel);
  if (logLifecycle) {
    console.log(`${formatPrefix(workerIndex, serviceLabel)} Stopping process (pid=${child.pid})`);
  }

  child.kill('SIGTERM');

  const exited = await waitForExit(child, 5_000);

  if (!exited) {
    console.warn(
      `${formatPrefix(workerIndex, serviceLabel)} Process did not exit after SIGTERM; sending SIGKILL`
    );
    child.kill('SIGKILL');
    await waitForExit(child, 5_000);
  }
}

function streamProcessOutput(
  child: ChildProcessWithoutNullStreams,
  workerIndex: number,
  serviceLabel: ServiceLabel
): void {
  const attach = (stream: Readable, source: 'stdout' | 'stderr') => {
    const lineStream = stream.pipe(split2());
    const prefix = `${formatPrefix(workerIndex, serviceLabel)}[${source}]`;

    const handleLine = (line: string) => {
      process.stdout.write(`${prefix} ${line}\n`);
    };

    lineStream.on('data', handleLine);
    lineStream.on('error', (error: unknown) => {
      process.stdout.write(
        `${prefix} <<stream error>> ${error instanceof Error ? error.message : String(error)}\n`
      );
    });

    const cleanup = () => {
      lineStream.off('data', handleLine);
      lineStream.destroy();
    };

    child.once('exit', cleanup);
    child.once('error', cleanup);
  };

  attach(child.stdout, 'stdout');
  attach(child.stderr, 'stderr');
}

function formatPrefix(workerIndex: number, serviceLabel: ServiceLabel): string {
  return `[worker-${workerIndex} ${serviceLabel}]`;
}

function capitalize(value: string): string {
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`;
}

function serviceShouldLogLifecycle(serviceLabel: ServiceLabel): boolean {
  if (serviceLabel === 'backend') {
    return process.env.PLAYWRIGHT_BACKEND_LOG_STREAM === 'true';
  }
  if (serviceLabel === 'frontend') {
    return process.env.PLAYWRIGHT_FRONTEND_LOG_STREAM === 'true';
  }
  if (serviceLabel === 'sse-gateway') {
    return process.env.PLAYWRIGHT_GATEWAY_LOG_STREAM === 'true';
  }
  return false;
}

async function waitForExit(
  child: ChildProcessWithoutNullStreams,
  timeoutMs: number
): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode) {
    return true;
  }

  return new Promise(resolve => {
    const timeout = setTimeout(() => {
      child.removeListener('exit', onExit);
      resolve(false);
    }, timeoutMs);

    const onExit = () => {
      clearTimeout(timeout);
      resolve(true);
    };

    child.once('exit', onExit);
  });
}

let frontendRepoRootCache: string | undefined;
let backendRepoRootCache: string | undefined;

function getFrontendRepoRoot(): string {
  if (!frontendRepoRootCache) {
    const currentDir = dirname(fileURLToPath(import.meta.url));
    frontendRepoRootCache = resolve(currentDir, '../../..');
  }
  return frontendRepoRootCache;
}

function getBackendRepoRoot(): string {
  if (!backendRepoRootCache) {
    backendRepoRootCache = resolve(getFrontendRepoRoot(), process.env.BACKEND_ROOT || '../backend');
  }
  return backendRepoRootCache;
}

const activeChildren = new Set<ChildProcessWithoutNullStreams>();
let cleanupHookRegistered = false;

function registerForCleanup(child: ChildProcessWithoutNullStreams): void {
  activeChildren.add(child);
  child.once('exit', () => {
    activeChildren.delete(child);
  });

  if (!cleanupHookRegistered) {
    cleanupHookRegistered = true;

    const shutdown = () => {
      for (const proc of activeChildren) {
        try {
          proc.kill('SIGTERM');
        } catch {
          // Ignore cleanup failures; process may already be terminating.
        }
      }
    };

    process.once('exit', shutdown);
    process.once('SIGINT', () => {
      shutdown();
      process.exit(130);
    });
    process.once('SIGTERM', () => {
      shutdown();
      process.exit(143);
    });
  }
}
