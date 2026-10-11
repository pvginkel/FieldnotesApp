// The operator's side (FR-28): a text field with Send. Ctrl+Enter sends; Enter breaks the line, as
// the browser has it. While a turn is in flight Send is withheld, not the field: the answer can be
// drafted while the session works.

import { useLayoutEffect, useRef } from 'react';
import { SendHorizontal } from 'lucide-react';

/** How many lines the field grows to before it scrolls. */
const LINES = 8;

interface ComposerProps {
  draft: string;
  working: boolean;
  busy: boolean;
  onDraft: (text: string) => void;
  onSend: () => void;
}

export function Composer({ draft, working, busy, onDraft, onSend }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const canSend = Boolean(draft.trim()) && !working && !busy;

  // The field grows with what is typed, to `LINES` lines, then scrolls.
  useLayoutEffect(() => {
    const field = ref.current;
    if (!field) return;
    const style = getComputedStyle(field);
    const px = (value: string) => parseFloat(value);
    const frame = px(style.borderTopWidth) + px(style.borderBottomWidth);
    const most = LINES * px(style.lineHeight) + px(style.paddingTop) + px(style.paddingBottom) + frame;
    field.style.height = 'auto';
    const needed = field.scrollHeight + frame;
    field.style.height = `${Math.min(needed, most)}px`;
    field.style.overflowY = needed > most ? 'auto' : 'hidden';
  }, [draft]);

  return (
    <div className="composer" data-testid="inbox.composer">
      <div className="column composer-row">
        <textarea
          ref={ref}
          rows={1}
          value={draft}
          onChange={(event) => onDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || !event.ctrlKey || event.nativeEvent.isComposing) return;
            event.preventDefault();
            if (canSend) onSend();
          }}
          data-testid="inbox.composer.text"
        />
        <button
          type="button"
          className="btn btn-primary send"
          disabled={!canSend}
          title={working ? 'Wait for the session to answer' : 'Send (Ctrl+Enter)'}
          onClick={onSend}
          data-testid="inbox.composer.send"
        >
          <SendHorizontal className="icon" aria-hidden="true" /> Send
        </button>
      </div>
    </div>
  );
}
