// The app bar's pills, on both screens (FR-33): an inbox sitting the API holds, and the actioner.
// Each shows only while it has something to show; its tooltip says what. Both are fetched on a
// timer, sooner while something is in flight. The Inbox pill leads to the inbox screen; the
// Actioner pill, while a start is pending or a run is held, to the actioner page on the triage
// screen, and once the actioner is idle with a failure, to the failure's dialog.

import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useGetInbox, useGetTriageActioner } from '@/lib/api/generated/hooks';
import type { InboxState_5430f38 } from '@/lib/api/generated/hooks';
import { failed, isBusy, type ActionerFailure, type ActionerState } from '@/lib/triage/actioner';
import { stamp } from '@/lib/triage/time';
import { ActionerFailureDialog } from './actioner-failure-dialog';

type InboxState = InboxState_5430f38;

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

function ActionerPill() {
  const { data } = useGetTriageActioner(undefined, {
    refetchInterval: (q) => ((q.state.data as ActionerState | undefined)?.state === 'idle' ? 20000 : 5000),
  });
  // The failure the dialog shows: the pill's when it was clicked, which Close clears and no other.
  const [shown, setShown] = useState<ActionerFailure | null>(null);

  let pill = null;
  if (data && isBusy(data)) {
    // Starting is a start pending: asked of the controller, or a skipped run's, waiting to be tried
    // again.
    const title =
      data.state === 'running'
        ? `The actioner is running, since ${stamp(data.since!)}`
        : data.failure
          ? `${failed(data.failure)}; it is tried again`
          : 'The actioner is starting';
    pill = (
      <Link
        to="/"
        search={{ actioner: true }}
        className="app-pill"
        data-state="working"
        title={title}
        data-testid="app-shell.pill.actioner"
      >
        <span className="dot" />
        Actioner
      </Link>
    );
  } else if (data?.failure) {
    const failure = data.failure;
    pill = (
      <button
        type="button"
        className="app-pill"
        data-state="failed"
        title={failed(failure)}
        aria-haspopup="dialog"
        onClick={() => setShown(failure)}
        data-testid="app-shell.pill.actioner"
      >
        <span className="dot" />
        Actioner
      </button>
    );
  }
  return (
    <>
      {pill}
      {shown && <ActionerFailureDialog failure={shown} onDone={() => setShown(null)} />}
    </>
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
