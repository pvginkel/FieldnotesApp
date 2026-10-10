// The app's two screens, side by side in the header: the triage stack and the inbox sitting
// (FR-33). Neither knows anything of the other.

import { Link } from '@tanstack/react-router';

export function ScreenNav() {
  return (
    <nav className="screens" aria-label="Screens" data-testid="app-shell.screens">
      <Link
        to="/"
        activeOptions={{ exact: true }}
        activeProps={{ className: 'active' }}
        data-testid="app-shell.screens.triage"
      >
        Triage
      </Link>
      <Link to="/inbox" activeProps={{ className: 'active' }} data-testid="app-shell.screens.inbox">
        Inbox
      </Link>
    </nav>
  );
}
