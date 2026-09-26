import { useDeploymentNotification } from '@/hooks/use-deployment-notification'

export function DeploymentNotificationBar() {
  const { isNewVersionAvailable, reloadApp } = useDeploymentNotification()

  if (!isNewVersionAvailable) {
    return null
  }

  return (
    <div
      className="w-full bg-blue-600 text-white px-4 py-3 text-center text-sm font-medium shadow-md"
      role="status"
      data-testid="deployment.banner"
    >
      A new version of the app is available.{' '}
      <button
        onClick={reloadApp}
        className="cursor-pointer underline hover:no-underline font-semibold focus:outline-hidden focus:ring-2 focus:ring-blue-300 focus:ring-offset-2 focus:ring-offset-blue-600 rounded px-1"
        type="button"
        data-testid="deployment.banner.reload"
      >
        Click reload to reload the app.
      </button>
    </div>
  )
}
