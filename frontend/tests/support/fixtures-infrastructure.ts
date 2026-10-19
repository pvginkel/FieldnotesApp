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
import { cloneSeed, databaseUrl, dropDatabase, RUN_PREFIX_VARIABLE } from './helpers/pg-admin';
import { AppShellPage } from './page-objects/app-shell-page';
import { startBackend, startFrontend } from './process/servers';
import {
  createBackendLogCollector,
  createFrontendLogCollector,
  type BackendLogCollector,
  type FrontendLogCollector,
} from './process/backend-logs';

type ServiceManager = {
  backendUrl: string;
  frontendUrl: string;
  backendLogs: BackendLogCollector;
  frontendLogs: FrontendLogCollector;
  disposeServices(): Promise<void>;
};

export type InfrastructureFixtures = {
  frontendUrl: string;
  backendUrl: string;
  backendLogs: BackendLogCollector;
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

          // showException logs 'Toast exception' outside production builds;
          // the toast itself is what a test asserts on.
          if (text.includes('Form submission error') || text.includes('Toast exception')) {
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
        const frontendStreamLogs =
          process.env.PLAYWRIGHT_FRONTEND_LOG_STREAM === 'true';
        const backendLogs = createBackendLogCollector({
          workerIndex: workerInfo.workerIndex,
          streamToConsole: backendStreamLogs,
        });
        const frontendLogs = createFrontendLogCollector({
          workerIndex: workerInfo.workerIndex,
          streamToConsole: frontendStreamLogs,
        });

        const previousBackend = process.env.BACKEND_URL;
        const previousFrontend = process.env.FRONTEND_URL;

        // The worker's own database, cloned from the run's seed (global
        // setup), when the backend has one.
        let workerDatabase: string | undefined;

        const cleanupWorkerDb = async () => {
          if (!workerDatabase) {
            return;
          }
          try {
            await dropDatabase(workerDatabase);
          } catch (error) {
            console.warn(
              `[worker-${workerInfo.workerIndex}] Failed to drop database ${workerDatabase}:`,
              error
            );
          } finally {
            workerDatabase = undefined;
          }
        };

        const runPrefix = process.env[RUN_PREFIX_VARIABLE];
        if (runPrefix) {
          try {
            workerDatabase = await cloneSeed(runPrefix, workerInfo.parallelIndex);
            backendLogs.log(`Using database ${workerDatabase}, cloned from the seed`);
          } catch (error) {
            backendLogs.dispose();
              frontendLogs.dispose();
            await cleanupWorkerDb();
            throw error;
          }
        }

        const backendPort = await getPort();
        const frontendPort = await getPort({ exclude: [backendPort] });

        // 1. Start the backend. It serves the API and the SSE stream; the
        //    frontend's port is allocated already, so its version URL is known.
        const backendPromise = (async () => {
          try {
            const backendHandle = await startBackend(workerInfo.workerIndex, {
              ...(workerDatabase ? { databaseUrl: databaseUrl(workerDatabase) } : {}),
              streamLogs: backendStreamLogs,
              port: backendPort,
              frontendVersionUrl: `http://127.0.0.1:${frontendPort}/version.json`,
            });
            backendLogs.attachStream(backendHandle.process.stdout, 'stdout');
            backendLogs.attachStream(backendHandle.process.stderr, 'stderr');
            backendLogs.log(`Backend listening on ${backendHandle.url}`);
            return backendHandle;
          } catch (error) {
            backendLogs.log(
              `Backend failed to start: ${(error as Error)?.message ?? String(error)}`
            );
            throw error;
          }
        })();

        // 2. Then the frontend, whose proxy sends /api, the SSE stream
        //    included, to the backend.
        const frontendPromise = backendPromise.then(async backendHandle => {
          frontendLogs.log(`Starting frontend against backend ${backendHandle.url}`);

          try {
            const frontendHandle = await startFrontend({
              workerIndex: workerInfo.workerIndex,
              backendUrl: backendHandle.url,
              excludePorts: [backendHandle.port],
              port: frontendPort,
              streamLogs: frontendStreamLogs,
            });
            frontendLogs.attachStream(frontendHandle.process.stdout, 'stdout');
            frontendLogs.attachStream(frontendHandle.process.stderr, 'stderr');
            frontendLogs.log(`Frontend listening on ${frontendHandle.url}`);
            return { backendHandle, frontendHandle };
          } catch (error) {
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
        let frontend:
          | Awaited<ReturnType<typeof startFrontend>>
          | undefined;

        try {
          const result = await frontendPromise;
          backend = result.backendHandle;
          frontend = result.frontendHandle;
        } catch (error) {
          backendLogs.dispose();
          frontendLogs.dispose();
          await cleanupWorkerDb();
          throw error;
        }

        if (!backend || !frontend) {
          backendLogs.dispose();
          frontendLogs.dispose();
          await cleanupWorkerDb();
          throw new Error(
            `[worker-${workerInfo.workerIndex}] Failed to start managed services`
          );
        }

        process.env.BACKEND_URL = backend.url;
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

          // Dispose in reverse order: frontend -> backend
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
          await backend.dispose();
          await cleanupWorkerDb();
        };

        const serviceManager: ServiceManager = {
          backendUrl: backend.url,
          frontendUrl: frontend.url,
          backendLogs,
          frontendLogs,
          disposeServices,
        };

        try {
          await use(serviceManager);
        } finally {
          await disposeServices();
          process.env.BACKEND_URL = previousBackend;
          process.env.FRONTEND_URL = previousFrontend;
          backendLogs.dispose();
          frontendLogs.dispose();
        }
      },
      { scope: 'worker', timeout: 300_000 },
    ],
  }
);

export const test = infrastructureFixtures;
export { expect } from '@playwright/test';
