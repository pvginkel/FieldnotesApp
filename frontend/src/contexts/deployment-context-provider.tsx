import { useState, useEffect, useCallback, useRef } from 'react'
import type { ReactNode } from 'react'
import { isTestMode } from '@/lib/config/test-mode'
import { useVersionSSE } from '../hooks/use-version-sse'
import { useSseContext } from './sse-context'
import { DeploymentContext } from './deployment-context-base'
import type { DeploymentContextValue } from './deployment-context-base'

type DeploymentBridge = {
  connect: (requestId?: string) => void
  disconnect: () => void
  getStatus: () => { isConnected: boolean; requestId: string | null }
  getRequestId: () => string | null
}

interface DeploymentProviderProps {
  children: ReactNode
}

export function DeploymentProvider({ children }: DeploymentProviderProps) {
  const [isNewVersionAvailable, setIsNewVersionAvailable] = useState(false)
  const [currentVersion, setCurrentVersion] = useState<string | null>(null)

  const { isConnected, version } = useVersionSSE()
  const sseContext = useSseContext()
  const { requestId } = sseContext

  const isConnectedRef = useRef(isConnected)
  const deploymentRequestIdRef = useRef<string | null>(requestId)
  const deploymentControlsRef = useRef<DeploymentBridge | null>(null)
  const sseContextRef = useRef(sseContext)

  useEffect(() => {
    isConnectedRef.current = isConnected
  }, [isConnected])

  useEffect(() => {
    deploymentRequestIdRef.current = requestId
  }, [requestId])

  useEffect(() => {
    sseContextRef.current = sseContext
  }, [sseContext])

  const checkForUpdates = useCallback(() => {
    // Trigger SSE reconnection (for window focus handler)
    sseContext.reconnect()
  }, [sseContext])

  const reloadApp = useCallback(() => {
    window.location.reload()
  }, [])

  // Handle version changes from SSE
  useEffect(() => {
    if (version) {
      if (currentVersion === null) {
        // First version received - set as current
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Intentional state synchronization from SSE
        setCurrentVersion(version)
      } else if (version !== currentVersion) {
        // Version changed - new version available
        setIsNewVersionAvailable(true)
      }
    }
  }, [version, currentVersion])

  // Handle window focus to trigger reconnection (production mode or SharedWorker test mode)
  useEffect(() => {
    const hasSharedWorkerParam = typeof window !== 'undefined' &&
      new URLSearchParams(window.location.search).has('__sharedWorker')
    const shouldAutoConnect = (!isTestMode() && !import.meta.env.DEV) || hasSharedWorkerParam
    if (!shouldAutoConnect) {
      return
    }

    const handleFocus = () => {
      checkForUpdates()
    }

    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [checkForUpdates])

  useEffect(() => {
    if (!isTestMode()) {
      return
    }

    if (typeof window === 'undefined') {
      return
    }

    const globalWindow = window as typeof window & {
      __deploymentSseControls?: DeploymentBridge | null
    }

    if (!deploymentControlsRef.current) {
      deploymentControlsRef.current = {
        connect: () => {
          // In test mode, manually trigger SSE connection
          // This allows tests to control connection timing and request ID
          sseContextRef.current.reconnect()
        },
        disconnect: () => {
          // No-op: disconnection is not exposed from SseContextProvider
          // Tests should rely on connection state rather than explicit disconnect
        },
        getStatus: () => ({
          isConnected: isConnectedRef.current,
          requestId: deploymentRequestIdRef.current
        }),
        getRequestId: () => {
          return deploymentRequestIdRef.current
        }
      }
      globalWindow.__deploymentSseControls = deploymentControlsRef.current
    } else {
      globalWindow.__deploymentSseControls = deploymentControlsRef.current
    }

    return () => {
      if (globalWindow.__deploymentSseControls === deploymentControlsRef.current) {
        delete globalWindow.__deploymentSseControls
      }
      deploymentControlsRef.current = null
    }
  }, [])

  const contextValue: DeploymentContextValue = {
    isNewVersionAvailable,
    currentVersion,
    deploymentRequestId: requestId,
    checkForUpdates,
    reloadApp
  }

  return (
    <DeploymentContext.Provider value={contextValue}>
      {children}
    </DeploymentContext.Provider>
  )
}
