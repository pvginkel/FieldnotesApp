/**
 * Root layout component.
 * Composes provider groups and renders the app shell.
 */

import { createRootRoute, Outlet } from '@tanstack/react-router';
import { DeploymentNotificationBar } from '@/components/primitives/deployment-notification-bar';
import { Providers } from '@/providers';

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  return (
    <Providers>
      <div
        className="flex h-screen flex-col overflow-hidden"
        data-testid="app-shell.root"
      >
        <DeploymentNotificationBar />
        <Outlet />
      </div>
    </Providers>
  );
}
