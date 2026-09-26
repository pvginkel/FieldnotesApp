/**
 * Infrastructure test fixtures.
 * Template-owned — provides generic test infrastructure (service management,
 * page enhancement, test event bridge, log collectors, toast helper,
 * file upload helper, deployment SSE, and auth factory).
 *
 * Domain fixtures in fixtures.ts extend these with app-specific page objects.
 */

/* eslint-disable react-hooks/rules-of-hooks, no-empty-pattern */
import { test as base } from '@playwright/test';
import type { WorkerInfo } from '@playwright/test';
import { copyFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import getPort from 'get-port';
import {
  TestEventCapture,
  createTestEventCapture,
  ensureTestEventBridge,
  releaseTestEventBridge,
} from './helpers/test-events';
import { ToastHelper, createToastHelper } from './helpers/toast-helpers';
import {
  DeploymentSseHelper,
  createDeploymentSseHelper,
} from './helpers/deployment-sse';
import { FileUploadHelper, createFileUploadHelper } from './helpers/file-upload';
import { AuthFactory } from './helpers/auth-factory';
import { AppShellPage } from './page-objects/app-shell-page';
import { startBackend, startFrontend, startSSEGateway } from './process/servers';
import {
  createBackendLogCollector,
  createFrontendLogCollector,
  createGatewayLogCollector,
  type BackendLogCollector,
  type FrontendLogCollector,
  type GatewayLogCollector,
} from './process/backend-logs';

type ServiceManager = {
  backendUrl: string;
  gatewayUrl: string;
  frontendUrl: string;
  backendLogs: BackendLogCollector;
  gatewayLogs: GatewayLogCollector;
  frontendLogs: FrontendLogCollector;
  sqliteDbPath?: string;
  disposeServices(): Promise<void>;
};

export type InfrastructureFixtures = {
  frontendUrl: string;
  backendUrl: string;
  gatewayUrl: string;
  backendLogs: BackendLogCollector;
  gatewayLogs: GatewayLogCollector;
  frontendLogs: FrontendLogCollector;
  sseTimeout: number;
  testEvents: TestEventCapture;
  toastHelper: ToastHelper;
  fileUploadHelper: FileUploadHelper;
  deploymentSse: DeploymentSseHelper;
  auth: AuthFactory;
  appShell: AppShellPage;
};

type InternalFixtures = {
  _serviceManager: ServiceManager;
};

export const infrastructureFixtures = base.extend<InfrastructureFixtures, InternalFixtures>({
    frontendUrl: async ({ _serviceManager }, use) => {
      await use(_serviceManager.frontendUrl);
    },

    backendUrl: async ({ _serviceManager }, use) => {
      await use(_serviceManager.backendUrl);
    },

    gatewayUrl: async ({ _serviceManager }, use) => {
      await use(_serviceManager.gatewayUrl);
    },

    backendLogs: [
      async ({ _serviceManager }, use, testInfo) => {
        const attachment = await _serviceManager.backendLogs.attachToTest(testInfo);
        try {
          await use(_serviceManager.backendLogs);
        } finally {
          await attachment.stop();
        }
      },
      { auto: true },
    ],

    gatewayLogs: [
      async ({ _serviceManager }, use, testInfo) => {
        const attachment = await _serviceManager.gatewayLogs.attachToTest(testInfo);
        try {
          await use(_serviceManager.gatewayLogs);
        } finally {
          await attachment.stop();
        }
      },
      { auto: true },
    ],

    frontendLogs: [
      async ({ _serviceManager }, use, testInfo) => {
        const attachment = await _serviceManager.frontendLogs.attachToTest(testInfo);
        try {
          await use(_serviceManager.frontendLogs);
        } finally {
          await attachment.stop();
        }
      },
      { auto: true },
    ],

    baseURL: async ({ frontendUrl }, use) => {
      await use(frontendUrl);
    },

    sseTimeout: async ({}, use) => {
      await use(35_000);
    },

    page: async ({ page, _serviceManager }, use) => {
      const expectedErrors: RegExp[] = [
        /Unable to preventDefault inside passive event listener invocation\./,
      ];
      const buffer = await ensureTestEventBridge(page);

      buffer.addEvent({
        kind: 'ui_state',
        scope: 'worker.services',
        phase: 'ready',
        metadata: {
          backendUrl: _serviceManager.backendUrl,
          gatewayUrl: _serviceManager.gatewayUrl,
          frontendUrl: _serviceManager.frontendUrl,
        },
        timestamp: new Date().toISOString(),
      });

      await page.exposeFunction('__registerExpectedError', (pattern: string, flags?: string) => {
        expectedErrors.push(new RegExp(pattern, flags));
      });

      page.on('console', msg => {
        if (msg.type() === 'error') {
          const text = msg.text();

          if (expectedErrors.some(pattern => pattern.test(text))) {
            return;
          }

          if (
            text.includes('409') ||
            text.includes('CONFLICT') ||
            text.includes('already exists') ||
            text.includes('duplicate') ||
            text.includes('cannot delete') ||
            text.includes('in use')
          ) {
            return;
          }

          if (text.includes('Form submission error')) {
            return;
          }

          // Allow 404 errors for recently deleted resources
          // These can occur during cleanup when React Query caches are being invalidated
          if (
            text.includes('404') ||
            text.includes('NOT FOUND')
          ) {
            return;
          }

          // Allow 401 errors during auth tests (expected when unauthenticated)
          if (text.includes('401') || text.includes('UNAUTHORIZED')) {
            return;
          }

          // Allow 403 errors during auth tests (expected for forbidden resources)
          if (text.includes('403') || text.includes('FORBIDDEN')) {
            return;
          }

          // Allow 400 errors (e.g. /api/auth/login when OIDC is disabled in test mode)
          if (text.includes('400') || text.includes('BAD REQUEST')) {
            return;
          }

          // Allow 500 errors during auth error tests
          if (text.includes('500') || text.includes('INTERNAL SERVER ERROR')) {
            return;
          }

          // Allow auth-related errors scoped to /api/auth/ endpoints.
          // These are expected during auth gate error/retry tests and OIDC flows.
          if (text.includes('/api/auth/')) {
            return;
          }

          throw new Error(`Console error: ${text}`);
        }
      });
      page.on('pageerror', err => {
        throw err;
      });

      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.addStyleTag({
        content: `*, *::before, *::after {
        transition-duration: 0s !important;
        animation-duration: 0s !important;
        animation-delay: 0s !important;
      }`,
      });

      let overflowError: Error | null = null;
      try {
        await use(page);
      } finally {
        overflowError = buffer.getOverflowError();
        expectedErrors.length = 0;
        releaseTestEventBridge(page);
      }

      if (overflowError) {
        throw overflowError;
      }
    },

    testEvents: async ({ page, _serviceManager }, use) => {
      const buffer = await ensureTestEventBridge(page);
      const testEvents = createTestEventCapture(page);
      await testEvents.startCapture();

      buffer.addEvent({
        kind: 'ui_state',
        scope: 'worker.services',
        phase: 'ready',
        metadata: {
          backendUrl: _serviceManager.backendUrl,
          gatewayUrl: _serviceManager.gatewayUrl,
          frontendUrl: _serviceManager.frontendUrl,
        },
        timestamp: new Date().toISOString(),
      });

      let overflowError: Error | null = null;
      try {
        await use(testEvents);
      } finally {
        await testEvents.stopCapture();
        overflowError = buffer.getOverflowError();
      }

      if (overflowError) {
        throw overflowError;
      }
    },

    toastHelper: async ({ page }, use) => {
      const toastHelper = createToastHelper(page);
      await use(toastHelper);
    },

    fileUploadHelper: async ({ page }, use) => {
      const fileUploadHelper = createFileUploadHelper(page);
      await use(fileUploadHelper);
    },

    deploymentSse: async ({ page }, use) => {
      const helper = createDeploymentSseHelper(page);
      try {
        await use(helper);
      } finally {
        try {
          await helper.disconnect();
        } catch {
          // Ignore cleanup failures; the page may have navigated away.
        }
      }
    },


    appShell: async ({ page }, use) => {
      await use(new AppShellPage(page));
    },

    auth: async ({ _serviceManager, page }, use) => {
      // Use frontend URL so cookies are set on the correct origin.
      // The /api/* routes are proxied to the backend.
      // Uses page.request to share cookies with the browser context.
      const factory = new AuthFactory(_serviceManager.frontendUrl, page);
      await use(factory);
    },

    _serviceManager: [
      async ({}, use: (value: ServiceManager) => Promise<void>, workerInfo: WorkerInfo) => {
        const backendStreamLogs =
          process.env.PLAYWRIGHT_BACKEND_LOG_STREAM === 'true';
        const gatewayStreamLogs =
          process.env.PLAYWRIGHT_GATEWAY_LOG_STREAM === 'true';
        const frontendStreamLogs =
          process.env.PLAYWRIGHT_FRONTEND_LOG_STREAM === 'true';
        const backendLogs = createBackendLogCollector({
          workerIndex: workerInfo.workerIndex,
          streamToConsole: backendStreamLogs,
        });
        const gatewayLogs = createGatewayLogCollector({
          workerIndex: workerInfo.workerIndex,
          streamToConsole: gatewayStreamLogs,
        });
        const frontendLogs = createFrontendLogCollector({
          workerIndex: workerInfo.workerIndex,
          streamToConsole: frontendStreamLogs,
        });

        const previousBackend = process.env.BACKEND_URL;
        const previousGateway = process.env.SSE_GATEWAY_URL;
        const previousFrontend = process.env.FRONTEND_URL;

        let workerDbDir: string | undefined;
        let workerDbPath: string | undefined;

        const cleanupWorkerDb = async () => {
          if (!workerDbDir) {
            return;
          }
          try {
            await rm(workerDbDir, { recursive: true, force: true });
          } catch (error) {
            console.warn(
              `[worker-${workerInfo.workerIndex}] Failed to remove temp SQLite data at ${workerDbDir}:`,
              error
            );
          } finally {
            workerDbDir = undefined;
            workerDbPath = undefined;
          }
        };

        const seedDbPath = process.env.PLAYWRIGHT_SEEDED_SQLITE_DB;
        if (seedDbPath) {
          try {
            workerDbDir = await mkdtemp(
              join(tmpdir(), `test-worker-${workerInfo.workerIndex}-`)
            );
            workerDbPath = join(workerDbDir, 'database.sqlite');
            await copyFile(seedDbPath, workerDbPath);
            backendLogs.log(`Using SQLite database copy at ${workerDbPath}`);
          } catch (error) {
            backendLogs.dispose();
            gatewayLogs.dispose();
            frontendLogs.dispose();
            await cleanupWorkerDb();
            throw error;
          }
        }

        const backendPort = await getPort();
        const gatewayPort = await getPort({ exclude: [backendPort] });
        const frontendPort = await getPort({ exclude: [backendPort, gatewayPort] });

        // Pre-compute URLs from allocated ports so we can wire them before any
        // service starts.  The backend's /health/readyz now checks SSE Gateway
        // reachability, so the gateway must be up before the backend can pass
        // its readiness probe.
        const backendUrl = `http://127.0.0.1:${backendPort}`;
        const gatewayUrl = `http://127.0.0.1:${gatewayPort}`;

        // Set gateway URL for backend before it starts
        process.env.SSE_GATEWAY_URL = gatewayUrl;

        // 1. Start SSE Gateway first — it only needs the backend URL for its
        //    callback config, not for its own readiness.
        const gatewayPromise = (async () => {
          gatewayLogs.log(`Starting SSE Gateway with callback to ${backendUrl}`);
          try {
            const gatewayHandle = await startSSEGateway({
              workerIndex: workerInfo.workerIndex,
              backendUrl,
              excludePorts: [backendPort],
              port: gatewayPort,
              streamLogs: gatewayStreamLogs,
            });
            gatewayLogs.attachStream(gatewayHandle.process.stdout, 'stdout');
            gatewayLogs.attachStream(gatewayHandle.process.stderr, 'stderr');
            gatewayLogs.log(`SSE Gateway listening on ${gatewayHandle.url}`);
            return gatewayHandle;
          } catch (error) {
            gatewayLogs.log(
              `SSE Gateway failed to start: ${(error as Error)?.message ?? String(error)}`
            );
            throw error;
          }
        })();

        // 2. Once the gateway is ready, start the backend (its readyz can now
        //    reach the gateway).
        const backendPromise = gatewayPromise.then(async gatewayHandle => {
          try {
            const backendHandle = await startBackend(workerInfo.workerIndex, {
              ...(workerDbPath ? { sqliteDbPath: workerDbPath } : {}),
              streamLogs: backendStreamLogs,
              port: backendPort,
              frontendVersionUrl: `http://127.0.0.1:${frontendPort}/version.json`,
            });
            backendLogs.attachStream(backendHandle.process.stdout, 'stdout');
            backendLogs.attachStream(backendHandle.process.stderr, 'stderr');
            backendLogs.log(`Backend listening on ${backendHandle.url}`);
            return { backendHandle, gatewayHandle };
          } catch (error) {
            await gatewayHandle.dispose();
            backendLogs.log(
              `Backend failed to start: ${(error as Error)?.message ?? String(error)}`
            );
            throw error;
          }
        });

        // 3. Finally start the frontend once both backend and gateway are up.
        const frontendPromise = backendPromise.then(async ({ backendHandle, gatewayHandle }) => {
          frontendLogs.log(`Starting frontend against backend ${backendHandle.url} and gateway ${gatewayHandle.url}`);

          // Set SSE_GATEWAY_URL before starting frontend so Vite can read it
          process.env.SSE_GATEWAY_URL = gatewayHandle.url;

          try {
            const frontendHandle = await startFrontend({
              workerIndex: workerInfo.workerIndex,
              backendUrl: backendHandle.url,
              excludePorts: [backendHandle.port, gatewayHandle.port],
              port: frontendPort,
              streamLogs: frontendStreamLogs,
            });
            frontendLogs.attachStream(frontendHandle.process.stdout, 'stdout');
            frontendLogs.attachStream(frontendHandle.process.stderr, 'stderr');
            frontendLogs.log(`Frontend listening on ${frontendHandle.url}`);
            return { backendHandle, gatewayHandle, frontendHandle };
          } catch (error) {
            await gatewayHandle.dispose();
            await backendHandle.dispose();
            frontendLogs.log(
              `Frontend failed to start: ${(error as Error)?.message ?? String(error)}`
            );
            throw error;
          }
        });

        let backend:
          | Awaited<ReturnType<typeof startBackend>>
          | undefined;
        let gateway:
          | Awaited<ReturnType<typeof startSSEGateway>>
          | undefined;
        let frontend:
          | Awaited<ReturnType<typeof startFrontend>>
          | undefined;

        try {
          const result = await frontendPromise;
          backend = result.backendHandle;
          gateway = result.gatewayHandle;
          frontend = result.frontendHandle;
        } catch (error) {
          backendLogs.dispose();
          gatewayLogs.dispose();
          frontendLogs.dispose();
          await cleanupWorkerDb();
          throw error;
        }

        if (!backend || !gateway || !frontend) {
          backendLogs.dispose();
          gatewayLogs.dispose();
          frontendLogs.dispose();
          await cleanupWorkerDb();
          throw new Error(
            `[worker-${workerInfo.workerIndex}] Failed to start managed services`
          );
        }

        process.env.BACKEND_URL = backend.url;
        process.env.SSE_GATEWAY_URL = gateway.url;
        process.env.FRONTEND_URL = frontend.url;

        if (workerInfo.project?.use) {
          const projectUse = workerInfo.project.use as { baseURL?: string };
          projectUse.baseURL = frontend.url;
        }

        let disposed = false;
        const disposeServices = async () => {
          if (disposed) {
            return;
          }
          disposed = true;

          // Dispose in reverse order: frontend -> gateway -> backend
          if (frontend) {
            try {
              await frontend.dispose();
            } catch (error) {
              console.warn(
                `[worker-${workerInfo.workerIndex}] Failed to dispose frontend:`,
                error
              );
            }
          }
          if (gateway) {
            try {
              await gateway.dispose();
            } catch (error) {
              console.warn(
                `[worker-${workerInfo.workerIndex}] Failed to dispose gateway:`,
                error
              );
            }
          }
          await backend.dispose();
          await cleanupWorkerDb();
        };

        const serviceManager: ServiceManager = {
          backendUrl: backend.url,
          gatewayUrl: gateway.url,
          frontendUrl: frontend.url,
          backendLogs,
          gatewayLogs,
          frontendLogs,
          disposeServices,
          sqliteDbPath: workerDbPath,
        };

        try {
          await use(serviceManager);
        } finally {
          await disposeServices();
          process.env.BACKEND_URL = previousBackend;
          process.env.SSE_GATEWAY_URL = previousGateway;
          process.env.FRONTEND_URL = previousFrontend;
          backendLogs.dispose();
          gatewayLogs.dispose();
          frontendLogs.dispose();
        }
      },
      { scope: 'worker', timeout: 120_000 },
    ],
  }
);

export const test = infrastructureFixtures;
export { expect } from '@playwright/test';
