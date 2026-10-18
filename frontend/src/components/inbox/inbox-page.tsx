// The inbox screen (FR-28 to FR-32): the tracker's inbox sitting, `/dev:inbox` in a headless
// session the API relays, held as a chat. Start, the transcript, a text field with Send, and End.

import { useEffect, useLayoutEffect, useRef, useState, type UIEvent } from 'react';
import { RefreshCw } from 'lucide-react';
import { CenterCard } from '@/components/triage/finish-card';
import { Toasts } from '@/components/triage/toasts';
import { useInbox } from '@/hooks/use-inbox';
import {
  applyTheme,
  loadBrowserState,
  saveBrowserState,
  watchSystemTheme,
  type Prefs,
} from '@/lib/triage/browser-state';
import { Composer } from './composer';
import { EndDialog } from './end-dialog';
import { InboxHeader } from './inbox-header';
import { StartCard } from './start-card';
import { Transcript } from './transcript';

// The theme preference, shared with the triage screen through its browser state.
function usePrefs() {
  const [prefs, setPrefs] = useState<Prefs>(() => loadBrowserState().prefs);
  useEffect(() => {
    applyTheme(prefs.theme);
    return watchSystemTheme(prefs.theme);
  }, [prefs.theme]);
  const setPref = <K extends keyof Prefs>(key: K, value: Prefs[K]) => {
    const next = { ...prefs, [key]: value };
    setPrefs(next);
    saveBrowserState({ ...loadBrowserState(), prefs: next });
  };
  return { prefs, setPref };
}

export function InboxPage() {
  const inbox = useInbox();
  const { query, state, sitting } = inbox;
  const { prefs, setPref } = usePrefs();
  // A pending End names the sitting it was asked for (by its start), so it lapses with that sitting.
  const [endingFor, setEndingFor] = useState<string | null>(null);
  const ending = sitting !== null && endingFor === sitting.began;
  const mainRef = useRef<HTMLElement>(null);

  // Whether the page follows the transcript's end: set where the page scrolls there itself, and
  // where the operator scrolls to it; cleared where the operator scrolls up.
  const following = useRef(true);
  const lastTop = useRef(0);
  const onScroll = (event: UIEvent<HTMLElement>) => {
    const main = event.currentTarget;
    const end = main.scrollHeight - main.scrollTop - main.clientHeight < 2;
    following.current = end || (following.current && main.scrollTop >= lastTop.current);
    lastTop.current = main.scrollTop;
  };

  // A new message is scrolled to its top, so a long answer is read from its start; the operator's
  // own message to the bottom.
  const count = sitting?.transcript.length ?? 0;
  const lastAuthor = sitting?.transcript[count - 1]?.author;
  const seen = useRef<number | null>(null);
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main || !count) return;
    if (seen.current === null) {
      following.current = true;
      main.scrollTop = main.scrollHeight; // the page opened on a sitting: its end
    } else if (count > seen.current) {
      if (lastAuthor === 'session') {
        const messages = main.querySelectorAll('.msg');
        messages[messages.length - 1]?.scrollIntoView({ block: 'start', behavior: 'smooth' });
      } else {
        following.current = true;
        main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' });
      }
    }
    seen.current = count;
  }, [count, lastAuthor]);

  // The working box grows at the transcript's end as its lines arrive: a page that follows the end
  // keeps its newest line and the spinner in view.
  const working = inbox.lines.length;
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (main && working && following.current) main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' });
  }, [working]);

  // A lost sitting: its line, at the end, in view.
  const closed = Boolean(sitting?.lost);
  useLayoutEffect(() => {
    if (closed) mainRef.current?.scrollTo({ top: mainRef.current.scrollHeight, behavior: 'smooth' });
  }, [closed]);

  const content = (() => {
    if (query.isPending) return <CenterCard title="" testId="inbox.loading" />;
    // Only before anything has loaded: a background refetch that fails keeps the sitting shown.
    if (!state) {
      return (
        <CenterCard title="The inbox did not load" testId="inbox.error">
          <div className="tally">{query.error?.message}</div>
          <button type="button" className="btn btn-soft" onClick={() => void query.refetch()}>
            <RefreshCw className="icon" aria-hidden="true" /> Try again
          </button>
        </CenterCard>
      );
    }
    if (!sitting) {
      return (
        <div className="column">
          <StartCard configured={state.configured} busy={inbox.busy} onStart={(scope) => void inbox.start(scope)} />
        </div>
      );
    }
    return (
      <div className="column inbox-column">
        <Transcript sitting={sitting} tracker={state.tracker} lines={inbox.lines} />
      </div>
    );
  })();

  return (
    <>
      <InboxHeader sitting={sitting} prefs={prefs} onEnd={() => setEndingFor(sitting?.began ?? null)} onPref={setPref} />
      <div className="stage inbox">
        <main className="main" ref={mainRef} onScroll={onScroll} data-testid="inbox.main">
          {content}
        </main>
        {sitting && !closed && (
          <Composer
            draft={inbox.draft}
            working={Boolean(sitting.turn)}
            busy={inbox.busy}
            onDraft={inbox.setDraft}
            onSend={() => void inbox.send()}
          />
        )}
        {sitting && closed && (
          <div className="composer restart-bar" data-testid="inbox.restart">
            <div className="column composer-row">
              <span className="restart-note">
                The session is gone, and Send with it. A new sitting clears this one.
              </span>
              <button
                type="button"
                className="btn btn-primary"
                disabled={inbox.busy || !state?.configured}
                onClick={() => void inbox.start('')}
                data-testid="inbox.restart.start"
              >
                Start a new sitting
              </button>
            </div>
          </div>
        )}
      </div>
      {ending && sitting && (
        <EndDialog
          sitting={sitting}
          onCancel={() => setEndingFor(null)}
          onEnd={() => {
            setEndingFor(null);
            void inbox.end();
          }}
        />
      )}
      <Toasts toasts={inbox.toasts} />
    </>
  );
}
