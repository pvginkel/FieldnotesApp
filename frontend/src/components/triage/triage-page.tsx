// The one screen: the triage stack, ported from the mockup (FieldnotesAppSpecs/mockups/triage-ui/).

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { useTriage } from '@/hooks/use-triage';
import { prosePattern, ProsePatternContext } from '@/lib/triage/prose-pattern';
import { ownersOf } from '@/lib/triage/queue';
import { CenterCard, FinishCard } from './finish-card';
import { KeysDialog } from './keys-dialog';
import { RulingPane } from './ruling-pane';
import { Toasts } from './toasts';
import { TriageCard } from './triage-card';
import { TriageHeader } from './triage-header';

export function TriagePage() {
  const triage = useTriage();
  const { view, current, noteRef, mainRef } = triage;
  const [keysOpen, setKeysOpen] = useState(false);
  const paneRef = useRef<HTMLElement>(null);

  const queue = triage.queue;
  const pattern = useMemo(() => prosePattern(queue ? ownersOf(queue) : []), [queue]);

  // The card starts below the pane, which lies over the scrolling area, and whatever scrolls into
  // view inside the card lands below it rather than behind it.
  useLayoutEffect(() => {
    const pane = paneRef.current;
    const main = mainRef.current;
    if (!pane || !main) return;
    const observer = new ResizeObserver(() => {
      main.style.paddingTop = `${pane.offsetHeight}px`;
      main.style.scrollPaddingTop = `${pane.offsetHeight + 16}px`;
    });
    observer.observe(pane);
    return () => observer.disconnect();
  }, [mainRef]);

  // The pane keeps clear of the scrolling area's scrollbar, one of the two gutters it reserves.
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main) return;
    const gutters = () =>
      document.documentElement.style.setProperty('--sb', `${(main.offsetWidth - main.clientWidth) / 2}px`);
    gutters();
    addEventListener('resize', gutters);
    return () => removeEventListener('resize', gutters);
  }, [mainRef]);

  // The keys. Re-bound on every render, so each press sees the page as it is.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const inNote = target === noteRef.current;

      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        // Not from the finish card, so a double press cannot send the batch: Submit is a click.
        event.preventDefault();
        if (view === 'card') triage.rule();
        return;
      }
      if (event.key === 'Escape') {
        if (keysOpen) setKeysOpen(false);
        else if (inNote) noteRef.current?.blur();
        return;
      }
      if (inNote || event.ctrlKey || event.metaKey || event.altKey) return;
      if (target.closest?.('select, input')) return;
      if (keysOpen) {
        setKeysOpen(false);
        return;
      }

      const onCard = view === 'card';
      const key = event.key;
      if (onCard && key === 'y') triage.setVerb('yes');
      else if (onCard && key === 'n') triage.setVerb('no');
      else if (onCard && key === 'l') triage.setVerb('later');
      else if (onCard && key === 'Tab' && !event.shiftKey) noteRef.current?.focus();
      else if (key === 'ArrowLeft') triage.previous();
      else if (onCard && key === 'ArrowRight') triage.next();
      else if (key === '?') setKeysOpen(true);
      else return;
      event.preventDefault();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  const content = (() => {
    switch (view) {
      case 'card':
        return <TriageCard item={current!} live={queue?.observations[current!.observation]} />;
      case 'finish':
        return (
          <FinishCard
            ruled={triage.ruledCount}
            submitting={triage.submitting}
            onPrevious={triage.previous}
            onSubmit={() => void triage.submit()}
            onRestart={triage.restart}
          />
        );
      case 'empty':
        return (
          <CenterCard title="Nothing to rule on" testId="triage.empty">
            <button
              type="button"
              className="btn btn-soft"
              disabled={triage.query.isFetching}
              onClick={() => void triage.query.refetch()}
              data-testid="triage.empty.refresh"
            >
              <RefreshCw className="icon" aria-hidden="true" /> Check again
            </button>
          </CenterCard>
        );
      case 'error':
        return (
          <CenterCard title="The queue did not load" testId="triage.error">
            <div className="tally">{triage.query.error?.message}</div>
            <button type="button" className="btn btn-primary btn-big" onClick={() => void triage.query.refetch()}>
              Try again
            </button>
          </CenterCard>
        );
      default:
        return <CenterCard title="" testId="triage.loading" />;
    }
  })();

  return (
    <ProsePatternContext.Provider value={pattern}>
      <TriageHeader
        order={triage.order}
        byId={triage.byId}
        shown={view === 'card' ? triage.idx : -1}
        skipped={triage.skipped}
        ruledCount={triage.ruledCount}
        showSubmit={view === 'card'}
        prefs={triage.prefs}
        onGoto={triage.go}
        onFinish={() => triage.go(triage.order.length)}
        onKeys={() => setKeysOpen(true)}
        onPref={triage.setPref}
      />
      <div className="stage">
        <RulingPane
          hidden={view !== 'card'}
          paneRef={paneRef}
          noteRef={noteRef}
          verbsRef={triage.verbsRef}
          draft={triage.draft}
          message={triage.message}
          canGoBack={triage.idx > 0}
          onPrevious={triage.previous}
          onNext={triage.next}
          onVerb={triage.setVerb}
          onNote={triage.setNote}
          onClear={triage.clearNote}
        />
        <main className="main" ref={mainRef} data-testid="triage.main">
          {content}
        </main>
      </div>
      <Toasts toasts={triage.toasts} />
      {keysOpen && <KeysDialog onClose={() => setKeysOpen(false)} />}
    </ProsePatternContext.Provider>
  );
}
