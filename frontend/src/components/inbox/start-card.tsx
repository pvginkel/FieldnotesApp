// No sitting: Start, with the scope the operator may type (FR-29). Without the controller's
// address, its token or the project the sitting is not configured, and Start is refused.

import { useState } from 'react';
import { Inbox } from 'lucide-react';

interface StartCardProps {
  configured: boolean;
  busy: boolean;
  onStart: (scope: string) => void;
}

export function StartCard({ configured, busy, onStart }: StartCardProps) {
  const [scope, setScope] = useState('');
  if (!configured) {
    return (
      <div className="center" data-testid="inbox.unconfigured">
        <Inbox className="inbox-mark" aria-hidden="true" />
        <h1>The inbox is not configured</h1>
        <div className="tally">The API needs the controller's address, its token and the store's project.</div>
        <button type="button" className="btn btn-primary btn-big" disabled>
          Start
        </button>
      </div>
    );
  }
  return (
    <form
      className="center"
      onSubmit={(event) => {
        event.preventDefault();
        onStart(scope.trim());
      }}
      data-testid="inbox.start"
    >
      <Inbox className="inbox-mark" aria-hidden="true" />
      <h1>Sort the inbox</h1>
      <div className="tally">Every project's new cards, hand-backs and your actions, in one message.</div>
      <input
        className="scope"
        value={scope}
        onChange={(event) => setScope(event.target.value)}
        placeholder="All projects, or name project keys or card ids"
        aria-label="Scope"
        data-testid="inbox.start.scope"
      />
      <button type="submit" className="btn btn-primary btn-big" disabled={busy} data-testid="inbox.start.submit">
        Start
      </button>
    </form>
  );
}
