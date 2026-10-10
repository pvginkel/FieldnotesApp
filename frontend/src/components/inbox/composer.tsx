// The operator's side (FR-28): a text field with Send. Enter sends; Shift+Enter and Alt+Enter break
// the line. While a turn is in flight Send is withheld, not the field: the answer can be drafted
// while the session works.

import { useLayoutEffect, useRef } from 'react';
import { SendHorizontal } from 'lucide-react';

interface ComposerProps {
  draft: string;
  working: boolean;
  busy: boolean;
  onDraft: (text: string) => void;
  onSend: () => void;
}

export function Composer({ draft, working, busy, onDraft, onSend }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const caret = useRef<number | null>(null);
  const canSend = Boolean(draft.trim()) && !working && !busy;

  // The field grows with what is typed, to a limit, then scrolls.
  useLayoutEffect(() => {
    const field = ref.current;
    if (!field) return;
    if (caret.current !== null) {
      field.selectionStart = field.selectionEnd = caret.current; // after an Alt+Enter line break
      caret.current = null;
    }
    field.style.height = 'auto';
    field.style.height = `${Math.min(field.scrollHeight + 2, 240)}px`;
    field.style.overflowY = field.scrollHeight + 2 > 240 ? 'auto' : 'hidden';
  }, [draft]);

  return (
    <div className="composer" data-testid="inbox.composer">
      <div className="column composer-row">
        <textarea
          ref={ref}
          rows={1}
          value={draft}
          placeholder={working ? 'The session is working; draft your answer meanwhile' : 'Answer in your own words'}
          onChange={(event) => onDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || event.nativeEvent.isComposing || event.shiftKey) return;
            event.preventDefault();
            if (event.altKey) {
              // The browser breaks the line on Shift+Enter only: Alt+Enter does it here.
              const field = event.currentTarget;
              const at = field.selectionStart;
              caret.current = at + 1;
              onDraft(`${draft.slice(0, at)}\n${draft.slice(field.selectionEnd)}`);
            } else if (canSend) {
              onSend();
            }
          }}
          data-testid="inbox.composer.text"
        />
        <button
          type="button"
          className="btn btn-primary send"
          disabled={!canSend}
          title={working ? 'Wait for the session to answer' : 'Send (Enter)'}
          onClick={onSend}
          data-testid="inbox.composer.send"
        >
          <SendHorizontal className="icon" aria-hidden="true" /> Send
        </button>
      </div>
    </div>
  );
}
