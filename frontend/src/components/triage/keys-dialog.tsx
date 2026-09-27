export function KeysDialog({ onClose }: { onClose: () => void }) {
  return (
    <div className="overlay" onClick={onClose} data-testid="triage.keys">
      <div className="dialog">
        <h2>Keys</h2>
        <dl className="keys">
          <dt><kbd>y</kbd> <kbd>n</kbd> <kbd>l</kbd></dt><dd>set the verb: yes, no, later</dd>
          <dt><kbd>Tab</kbd></dt><dd>to the note</dd>
          <dt><kbd>Esc</kbd></dt><dd>out of the note</dd>
          <dt><kbd>Ctrl</kbd>+<kbd>Enter</kbd></dt><dd>rule and show the next card</dd>
          <dt><kbd>←</kbd></dt><dd>previous</dd>
          <dt><kbd>→</kbd></dt><dd>skip (next, on a ruled card)</dd>
          <dt><kbd>?</kbd></dt><dd>this list</dd>
        </dl>
      </div>
    </div>
  );
}
