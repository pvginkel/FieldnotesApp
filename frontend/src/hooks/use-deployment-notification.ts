import { useContext } from 'react'
import {
  DeploymentContext,
  type DeploymentContextValue,
} from '@/contexts/deployment-context'

export function useDeploymentNotification(): DeploymentContextValue {
  const context = useContext(DeploymentContext)
  if (!context) {
    throw new Error('useDeploymentNotification must be used within a DeploymentProvider')
  }
  return context
}
