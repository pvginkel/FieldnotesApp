/**
 * The backend's UI setup, from GET /api/ui-setup: the deployment stage, and
 * whether the UI may show internal detail (the internals fact: true when the
 * stage isn't `prd`, or when the backend runs with HOMELAB_INTERNAL).
 */

import { useGetAuthSelf, useGetUiSetup } from '@/lib/api/generated/hooks';

export interface UiSetup {
  /** Deployment stage: `local-dev`, `dev`, `uat`, `prd`, `local-tst` or `tst`. */
  stage: string;
  /** Whether the UI may show internal detail, such as error diagnostics. */
  exposeInternals: boolean;
}

/**
 * Read the UI setup once the user is signed in, and keep it for the session.
 *
 * Returns null until it has loaded, and on any failure, a 403 for a signed-in
 * user with none of the app's roles included. Consumers treat null as
 * "no stage badge, no diagnostics".
 */
export function useUiSetup(): UiSetup | null {
  // The toast container renders outside AuthProvider, so the sign-in state
  // comes from AuthProvider's /api/auth/self query in the cache. Disabled,
  // this observer never fetches it itself. Asking for the UI setup before
  // sign-in would trip the API client's 401-to-login redirect.
  const { isSuccess: isSignedIn } = useGetAuthSelf(undefined, { enabled: false });
  const { data, isSuccess } = useGetUiSetup(undefined, {
    enabled: isSignedIn,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  if (!isSuccess || !data) {
    return null;
  }

  return {
    stage: data.stage,
    exposeInternals: data.expose_internals,
  };
}
