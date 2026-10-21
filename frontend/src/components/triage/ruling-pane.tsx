import { useLayoutEffect, type RefObject } from 'react';
import type { Draft } from '@/lib/triage/browser-state';
import type { Verb } from '@/lib/triage/queue';

interface RulingPaneProps {
  hidden: boolean;
  paneRef: RefObject<HTMLElement | null>;
  noteRef: RefObject<HTMLTextAreaElement | null>;
  verbsRef: RefObject<HTMLDivElement | null>;
  draft: Draft;
  message: string | null;
  canGoBack: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onVerb: (verb: Verb) => void;
  onNote: (note: string) => void;
  onClear: () => void;
}

const VERBS: { verb: Verb; key: string }[] = [
  { verb: 'yes', key: 'y' },
  { verb: 'no', key: 'n' },
  { verb: 'later', key: 'l' },
];

const PLACEHOLDERS: Record<Verb, string> = {
  yes: 'Anything the actioner should know.',
  no: 'Why not?',
  later: 'What should bring it back?',
};

// The pane lies over the top of the scrolling area, so the card scrolls under it, but it is not
// inside it: a focus or the caret in the note can never scroll the card.
export function RulingPane({
  hidden,
  paneRef,
  noteRef,
  verbsRef,
  draft,
  message,
  canGoBack,
  onPrevious,
  onNext,
  onVerb,
  onNote,
  onClear,
}: RulingPaneProps) {
  // One line at rest; a note that wraps grows the field, up to its max-height.
  useLayoutEffect(() => {
    const note = noteRef.current;
    if (!note) return;
    note.style.height = 'auto';
    note.style.height = `${note.scrollHeight + 2}px`;
    note.style.overflowY = note.scrollHeight + 2 > 160 ? 'auto' : 'hidden';
  }, [draft.note, hidden, noteRef]);

  return (
    <section className="pane" ref={paneRef} hidden={hidden} data-testid="triage.pane">
      {/* Frosted glass: stacked blurs that start below the note and build up behind the
          controls, with a tint that fades the same way. */}
      <div className="glass" aria-hidden="true"><i></i><i></i><i></i><b></b></div>
      <div className="column">
        <div className="pane-row">
          <button
            type="button"
            className="btn btn-soft"
            disabled={!canGoBack}
            onClick={onPrevious}
            data-testid="triage.previous"
          >
            <kbd>←</kbd> previous
          </button>
          <div className="verbs" ref={verbsRef}>
            {VERBS.map(({ verb, key }) => (
              <button
                key={verb}
                type="button"
                className={draft.verb === verb ? 'verb on' : 'verb'}
                aria-pressed={draft.verb === verb}
                onClick={() => onVerb(verb)}
                data-testid={`triage.verb.${verb}`}
              >
                <kbd>{key}</kbd> {verb}
              </button>
            ))}
          </div>
          <button type="button" className="btn btn-soft" onClick={onNext} data-testid="triage.next">
            {draft.verb ? 'next' : 'skip'} <kbd>→</kbd>
          </button>
        </div>
        <div className="note-wrap">
          <textarea
            id="note"
            ref={noteRef}
            rows={1}
            spellCheck
            className={message ? 'invalid' : undefined}
            placeholder={message || (draft.verb ? PLACEHOLDERS[draft.verb] : '')}
            value={draft.note}
            onChange={(event) => onNote(event.target.value)}
            data-testid="triage.note"
          />
          <button
            type="button"
            className="note-clear"
            title="Clear the note"
            aria-label="Clear the note"
            hidden={!draft.note}
            onClick={onClear}
            data-testid="triage.note.clear"
          >
            ×
          </button>
        </div>
      </div>
    </section>
  );
}
