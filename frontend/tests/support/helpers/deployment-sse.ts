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
  resetRequestId: () => Promise<void>;
}

const DEFAULT_CONNECTION_TIMEOUT = 20_000;
const BRIDGE_WAIT_TIMEOUT = 5_000;
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
    await waitForBridge();
    await page.evaluate(() => {
      const controls = window.__deploymentSseControls;
      if (!controls) {
        throw new Error(BRIDGE_MISSING_ERROR);
      }
      controls.disconnect();
    });
  };

  const ensureConnected = async (options?: { timeout?: number }): Promise<DeploymentSseStatus> => {
    const status = await getStatus();
    if (!status.isConnected) {
      await connect();
      await page.waitForFunction(() => {
        const controls = window.__deploymentSseControls;
        if (!controls) {
          throw new Error(BRIDGE_MISSING_ERROR);
        }
        return controls.getStatus().isConnected;
      }, undefined, { timeout: options?.timeout ?? DEFAULT_CONNECTION_TIMEOUT });
    }
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

  const resetRequestId = async (): Promise<void> => {
    await resetDeploymentRequestId(page);
  };

  return {
    connect,
    disconnect,
    ensureConnected,
    getStatus,
    getRequestId,
    resetRequestId,
  };
}
