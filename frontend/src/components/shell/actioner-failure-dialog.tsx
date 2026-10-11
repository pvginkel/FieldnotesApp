// The actioner's failure, opened from its red pill (FR-33): what ended without succeeding, when,
// and why. Close clears it through `POST /api/triage/actioner/clear`, naming the failure shown, so
// a newer one recorded meanwhile stays; the reply is the actioner's state, which the pill shows. A
// clear that fails is the template's error toast, and the dialog stays. Cancel leaves both. It is
// rendered under the body: the header's colours and buttons would otherwise style it.

import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useQueryClient } from '@tanstack/react-query';
import { usePostTriageActionerClear } from '@/lib/api/generated/hooks';
import { whatFailed, type ActionerFailure, type ActionerState } from '@/lib/triage/actioner';
import { stamp } from '@/lib/triage/time';

const KEY = ['getTriageActioner', undefined];

interface ActionerFailureDialogProps {
  failure: ActionerFailure;
  onDone: () => void;
}

export function ActionerFailureDialog({ failure, onDone }: ActionerFailureDialogProps) {
  const queryClient = useQueryClient();
  const clear = usePostTriageActionerClear({
    onSuccess: async (state: unknown) => {
      // A fetch in flight would answer the actioner as it stood before the clear.
      await queryClient.cancelQueries({ queryKey: KEY });
      queryClient.setQueryData<ActionerState>(KEY, state as ActionerState);
      onDone();
    },
  });

  // Modal for the keys too: taken before the document's listeners, so the triage keys do not rule
  // the card beneath. A key's default action, such as Tab's or a button's Enter, still happens.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      event.stopPropagation();
      if (event.key === 'Escape') onDone();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onDone]);

  return createPortal(
    <div className="overlay" onClick={onDone} data-testid="app-shell.actioner-failure">
      <div
        className="dialog failure-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="actioner-failure-what"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="actioner-failure-what" data-testid="app-shell.actioner-failure.what">
          {whatFailed(failure)}
        </h2>
        <dl className="failure-facts">
          <dt>When</dt>
          <dd data-testid="app-shell.actioner-failure.when">{stamp(failure.at)}</dd>
          {failure.reason && (
            <>
              <dt>Reason</dt>
              <dd data-testid="app-shell.actioner-failure.reason">{failure.reason}</dd>
            </>
          )}
        </dl>
        <p className="failure-note">Close clears it, and the pill with it.</p>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onDone} autoFocus data-testid="app-shell.actioner-failure.cancel">
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={clear.isPending}
            onClick={() => clear.mutate({ body: failure })}
            data-testid="app-shell.actioner-failure.close"
          >
            Close
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
