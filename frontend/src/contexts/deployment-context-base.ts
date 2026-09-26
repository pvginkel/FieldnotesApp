import { createContext } from 'react';

export interface DeploymentContextValue {
  isNewVersionAvailable: boolean;
  currentVersion: string | null;
  deploymentRequestId: string | null;
  checkForUpdates: () => void;
  reloadApp: () => void;
}

export const DeploymentContext = createContext<DeploymentContextValue | undefined>(undefined);
