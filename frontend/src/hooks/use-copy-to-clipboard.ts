import { useState, useCallback } from 'react';

type CopyState = 'idle' | 'success' | 'error';

/**
 * Hook for copying text to the clipboard with visual feedback state.
 *
 * Returns a `copyState` that transitions idle → success/error → idle
 * after `resetDelay` ms, plus a `reset()` to clear early (e.g. on dialog close).
 *
 * @example
 * const { copyState, copy } = useCopyToClipboard();
 * <button onClick={() => copy(text)}>
 *   {copyState === 'success' ? <Check /> : <Copy />}
 * </button>
 */
export function useCopyToClipboard(resetDelay = 1500) {
  const [copyState, setCopyState] = useState<CopyState>('idle');

  const copy = useCallback(
    (text: string) => {
      try {
        void navigator.clipboard.writeText(text).then(
          () => {
            setCopyState('success');
            setTimeout(() => setCopyState('idle'), resetDelay);
          },
          () => {
            setCopyState('error');
            setTimeout(() => setCopyState('idle'), resetDelay);
          },
        );
      } catch {
        setCopyState('error');
        setTimeout(() => setCopyState('idle'), resetDelay);
      }
    },
    [resetDelay],
  );

  const reset = useCallback(() => setCopyState('idle'), []);

  return { copyState, copy, reset } as const;
}

export type { CopyState };
