import { CircleHelp } from 'lucide-react';
import { isRuled, type Item } from '@/lib/triage/queue';
import { UserMenu } from './user-menu';
import type { Prefs } from '@/lib/triage/browser-state';

interface TriageHeaderProps {
  order: string[];
  byId: Map<string, Item>;
  shown: number; // the card shown, or -1
  skipped: Record<string, string>;
  ruledCount: number;
  showSubmit: boolean;
  submitting: boolean;
  prefs: Prefs;
  onGoto: (idx: number) => void;
  onSubmit: () => void;
  onKeys: () => void;
  onPref: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void;
}

export function TriageHeader(props: TriageHeaderProps) {
  const { order, byId, shown, skipped } = props;
  return (
    <header className="topbar" data-testid="triage.header">
      <div className="brand"><img className="logo" src="/fieldnotes-alt.svg" alt="" />Fieldnotes</div>
      <div className="progress" data-testid="triage.progress">
        {order.length > 0 && (
          // One segment per card, in the order they are visited: filled once it is ruled, darker
          // once it is skipped, with a dashed line under the card shown. A click shows its card.
          <div className="bar">
            {order.map((id, i) => {
              const item = byId.get(id);
              const state = !item ? '' : isRuled(item) ? 'ruled' : skipped[id] ? 'skipped' : '';
              return (
                <button
                  key={id}
                  type="button"
                  className={[state, i === shown ? 'here' : ''].join(' ').trim() || undefined}
                  title={item?.headline}
                  aria-label={`Card ${i + 1}`}
                  onClick={() => props.onGoto(i)}
                  data-testid="triage.progress.segment"
                  data-state={state || 'open'}
                />
              );
            })}
          </div>
        )}
      </div>
      {/* The finish card has its own Submit, and the empty page has nothing to submit: the
          header's is hidden on both. */}
      <button
        className="btn btn-primary"
        id="submit-top"
        type="button"
        hidden={!props.showSubmit}
        disabled={props.ruledCount === 0 || props.submitting}
        onClick={props.onSubmit}
        data-testid="triage.header.submit"
      >
        Submit
      </button>
      <button
        className="help"
        type="button"
        title="Keys (?)"
        aria-label="Keys"
        onClick={(event) => {
          event.currentTarget.blur(); // no focus ring left on the icon once the list closes
          props.onKeys();
        }}
        data-testid="triage.header.keys"
      >
        <CircleHelp className="icon" aria-hidden="true" />
      </button>
      <UserMenu prefs={props.prefs} onPref={props.onPref} />
    </header>
  );
}
