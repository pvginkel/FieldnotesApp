import type { ReactNode } from 'react';

// The finish card is the submit and little else: the header already has the progress.
export function FinishCard(props: { ruled: number; submitting: boolean; onPrevious: () => void; onSubmit: () => void; onRestart: () => void }) {
  const { ruled } = props;
  return (
    <div className="column" data-testid="triage.finish">
      <nav className="nav">
        <button type="button" className="btn btn-soft" onClick={props.onPrevious} data-testid="triage.finish.previous">
          <kbd>←</kbd> previous
        </button>
      </nav>
      <div className="center">
        {ruled > 0 && <div className="party" aria-hidden="true">🎉</div>}
        <h1>{ruled ? 'Ready to submit' : 'Nothing ruled yet'}</h1>
        {ruled ? (
          <button
            type="button"
            className="btn btn-primary btn-big"
            disabled={props.submitting}
            onClick={props.onSubmit}
            data-testid="triage.finish.submit"
          >
            Submit {ruled}
          </button>
        ) : (
          <button type="button" className="btn btn-primary btn-big" onClick={props.onRestart} data-testid="triage.finish.restart">
            Start over
          </button>
        )}
      </div>
    </div>
  );
}

/** A page with nothing to rule on: the empty queue, and the queue loading or failing to. */
export function CenterCard({ title, children, testId }: { title: string; children?: ReactNode; testId: string }) {
  return (
    <div className="column" data-testid={testId}>
      <div className="center">
        <h1>{title}</h1>
        {children}
      </div>
    </div>
  );
}
