// The app bar's pills, on both screens (FR-33): an inbox sitting the API holds, and the actioner.
// Each shows only while it has something to show; its tooltip says what. Both are fetched on a
// timer, sooner while something is in flight.

import { Link } from '@tanstack/react-router';
import { useGetInbox, useGetTriageActioner } from '@/lib/api/generated/hooks';
import type {
  ActionerState_0c09c4d,
  ActionerState_0c09c4d_ActionerFailure,
  InboxState_5430f38,
} from '@/lib/api/generated/hooks';
import { stamp } from '@/lib/triage/time';

type InboxState = InboxState_5430f38;
type ActionerState = ActionerState_0c09c4d;

function InboxPill() {
  const { data } = useGetInbox(undefined, {
    refetchInterval: (q) => ((q.state.data as InboxState | undefined)?.sitting?.turn ? 3000 : 15000),
  });
  const sitting = data?.sitting;
  if (!sitting) return null;
  const [state, title] = sitting.lost
    ? ['lost', `The inbox sitting was lost at ${stamp(sitting.lost.at)}: ${sitting.lost.reason}`]
    : sitting.turn
      ? ['working', 'The inbox session is working']
      : ['waiting', 'The inbox session waits for your answer'];
  return (
    <Link to="/inbox" className="app-pill" data-state={state} title={title} data-testid="app-shell.pill.inbox">
      <span className="dot" />
      Inbox
    </Link>
  );
}

/** A run that ended without succeeding, or a start the controller refused (FR-27). */
function failed(failure: ActionerState_0c09c4d_ActionerFailure): string {
  const what =
    failure.outcome === 'refused'
      ? "The controller refused the actioner's start"
      : `The actioner's last run ${failure.outcome === 'skipped' ? 'was skipped' : 'failed'}`;
  return `${what} at ${stamp(failure.at)}${failure.reason ? `: ${failure.reason}` : ''}`;
}

function ActionerPill() {
  const { data } = useGetTriageActioner(undefined, {
    refetchInterval: (q) => ((q.state.data as ActionerState | undefined)?.state === 'idle' ? 20000 : 5000),
  });
  if (!data) return null;
  let state: string;
  let title: string;
  if (data.state === 'running') {
    state = 'working';
    title = `The actioner is running, since ${stamp(data.since!)}`;
  } else if (data.state === 'starting') {
    // A start pending: asked of the controller, or a skipped run's, waiting to be tried again.
    state = 'working';
    title = data.failure ? `${failed(data.failure)}; it is tried again` : 'The actioner is starting';
  } else if (data.failure) {
    state = 'failed';
    title = failed(data.failure);
  } else {
    return null;
  }
  return (
    <span className="app-pill" data-state={state} title={title} data-testid="app-shell.pill.actioner">
      <span className="dot" />
      Actioner
    </span>
  );
}

export function AppPills() {
  return (
    <div className="app-pills">
      <InboxPill />
      <ActionerPill />
    </div>
  );
}
