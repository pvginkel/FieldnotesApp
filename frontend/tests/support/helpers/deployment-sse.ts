import { Page } from '@playwright/test';
import { resetDeploymentRequestId } from './deployment-reset';

export interface DeploymentSseStatus {
  isConnected: boolean;
  requestId: string | null;
}

export interface DeploymentSseHelper {
  connect: (requestId?: string) => Promise<void>;
  disconnect: () => Promise<void>;
  ensureConnected: (options?: { timeout?: number }) => Promise<DeploymentSseStatus>;
  getStatus: () => Promise<DeploymentSseStatus>;
  getRequestId: () => Promise<string | null>;
  waitForCurrentVersion: (version: string, options?: { timeout?: number }) => Promise<void>;
  resetRequestId: () => Promise<void>;
}

const DEFAULT_CONNECTION_TIMEOUT = 30_000;
const BRIDGE_WAIT_TIMEOUT = 30_000;
const MOUNT_CONNECTION_GRACE = 10_000;
const BRIDGE_MISSING_ERROR = 'Deployment SSE controls bridge is not registered on window.';

function ensureSerializableRequestId(requestId?: string): { requestId: string | null } {
  return { requestId: typeof requestId === 'string' ? requestId : null };
}

export function createDeploymentSseHelper(page: Page): DeploymentSseHelper {
  const waitForBridge = async (timeout = BRIDGE_WAIT_TIMEOUT): Promise<void> => {
    await page.waitForFunction(() => Boolean(window.__deploymentSseControls), undefined, {
      timeout,
    });
  };

  const getStatus = async (): Promise<DeploymentSseStatus> => {
    await waitForBridge();
    return page.evaluate(() => {
      const controls = window.__deploymentSseControls;
      if (!controls) {
        throw new Error(BRIDGE_MISSING_ERROR);
      }
      return controls.getStatus();
    });
  };

  const connect = async (requestId?: string): Promise<void> => {
    await waitForBridge();
    await page.evaluate(({ requestId }) => {
      const controls = window.__deploymentSseControls;
      if (!controls) {
        throw new Error(BRIDGE_MISSING_ERROR);
      }
      controls.connect(typeof requestId === 'string' ? requestId : undefined);
    }, ensureSerializableRequestId(requestId));
  };

  const disconnect = async (): Promise<void> => {
    // The bridge can legitimately be missing here: a test that navigated away
    // from the app, or never loaded it, leaves no bridge to tear down. The
    // underlying `disconnect()` exposed by the bridge is a documented no-op
    // anyway, so we just return when the bridge is gone rather than blocking
    // on a 30 s wait that will never satisfy.
    const hasBridge = await page.evaluate(() => Boolean(window.__deploymentSseControls));
    if (!hasBridge) {
      return;
    }
    await page.evaluate(() => {
      const controls = window.__deploymentSseControls;
      if (!controls) {
        return;
      }
      controls.disconnect();
    });
  };

  const ensureConnected = async (options?: { timeout?: number }): Promise<DeploymentSseStatus> => {
    const timeout = options?.timeout ?? DEFAULT_CONNECTION_TIMEOUT;

    // Wait for bridge to exist before triggering connect
    await waitForBridge(timeout);

    // The provider connects on mount, and the app renders before the stream is
    // ready (SseGate doesn't wait), so the bridge usually exists while the
    // first stream is still connecting. connect() would replace that stream,
    // so give it a moment first.
    const connectedOnMount = await page
      .waitForFunction(() => window.__deploymentSseControls?.getStatus().isConnected === true, undefined, {
        timeout: Math.min(timeout, MOUNT_CONNECTION_GRACE),
      })
      .then(
        () => true,
        () => false
      );
    if (!connectedOnMount) {
      await connect();
    }

    // Wait for both bridge existence AND isConnected in a single poll loop.
    // This handles the race where the bridge is registered but refs haven't
    // been populated yet (useEffect timing).
    await page.waitForFunction(() => {
      const controls = window.__deploymentSseControls;
      return controls != null && controls.getStatus().isConnected;
    }, undefined, { timeout });

    return getStatus();
  };

  const getRequestId = async (): Promise<string | null> => {
    await waitForBridge();
    return page.evaluate(() => {
      const controls = window.__deploymentSseControls;
      if (!controls) {
        throw new Error(BRIDGE_MISSING_ERROR);
      }
      return controls.getRequestId();
    });
  };

  // The deployment provider takes the first version it receives as the
  // current one, and only a different version raises the banner. A spec that
  // sends a baseline and then a newer version waits here in between, or the
  // provider can take the newer one as its first.
  const waitForCurrentVersion = async (
    version: string,
    options?: { timeout?: number }
  ): Promise<void> => {
    const timeout = options?.timeout ?? DEFAULT_CONNECTION_TIMEOUT;
    await waitForBridge(timeout);
    await page.waitForFunction(
      expected => window.__deploymentSseControls?.getCurrentVersion() === expected,
      version,
      { timeout }
    );
  };

  const resetRequestId = async (): Promise<void> => {
    await resetDeploymentRequestId(page);
  };

  return {
    connect,
    disconnect,
    ensureConnected,
    getStatus,
    getRequestId,
    waitForCurrentVersion,
    resetRequestId,
  };
}
