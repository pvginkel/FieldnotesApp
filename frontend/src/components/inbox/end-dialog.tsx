// End clears the sitting, its transcript with it (FR-31): asked first.

import { useEffect } from 'react';
import type { Sitting } from '@/hooks/use-inbox';

interface EndDialogProps {
  sitting: Sitting;
  onCancel: () => void;
  onEnd: () => void;
}

export function EndDialog({ sitting, onCancel, onEnd }: EndDialogProps) {
  // A lost sitting's session is gone.
  const live = !sitting.lost;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  return (
    <div className="overlay" onClick={onCancel} data-testid="inbox.end-dialog">
      <div className="dialog end-dialog" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <h2>End the sitting?</h2>
        <p>
          {live ? 'The session ends and the transcript goes with it.' : 'The transcript goes with it.'} What the
          session recorded stays on the cards.
          {sitting.turn && ' The turn in flight gets no answer.'}
          {sitting.started_environment && live && ` ${sitting.environment} was started for this sitting and is stopped, unless another session is working in it.`}
        </p>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onCancel} autoFocus>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={onEnd} data-testid="inbox.end-dialog.end">
            End the sitting
          </button>
        </div>
      </div>
    </div>
  );
}
