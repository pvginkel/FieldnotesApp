import type { TestEvent } from '@/lib/test/test-events';

declare global {
  interface ImportMetaEnv {
    readonly VITE_TEST_MODE: 'true' | 'false';
  }
}

type TestModeEnabled = Extract<ImportMetaEnv['VITE_TEST_MODE'], 'true'> extends never ? false : true;

type PlaywrightEventBinding = TestModeEnabled extends true
  ? (event: TestEvent) => void | Promise<void>
  : never;

type PlaywrightResetDeploymentRequestIdBinding = TestModeEnabled extends true ? () => void : never;

type DeploymentSseStatus = {
  isConnected: boolean;
  requestId: string | null;
};

type PlaywrightDeploymentSseControls = TestModeEnabled extends true
  ? {
      connect: (requestId?: string) => void;
      disconnect: () => void;
      getStatus: () => DeploymentSseStatus;
      getRequestId: () => string | null;
    }
  : never;

declare global {
  interface Window {
    __playwright_emitTestEvent?: PlaywrightEventBinding;
    __resetDeploymentRequestId?: PlaywrightResetDeploymentRequestIdBinding;
    __deploymentSseControls?: PlaywrightDeploymentSseControls;
  }
}

export {};
