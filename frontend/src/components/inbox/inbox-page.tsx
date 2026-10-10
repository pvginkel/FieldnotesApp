// The inbox screen (FR-28 to FR-32): the tracker's inbox sitting, `/dev:inbox` in a headless
// session the API relays, held as a chat. Start, the transcript, a text field with Send, and End.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
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
  const [ending, setEnding] = useState(false);
  const mainRef = useRef<HTMLElement>(null);

  // A new message is scrolled to its top, so a long answer is read from its start; the working
  // line and the operator's own message to the bottom.
  const count = sitting?.transcript.length ?? 0;
  const lastAuthor = sitting?.transcript[count - 1]?.author;
  const seen = useRef<number | null>(null);
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main || !count) return;
    if (seen.current === null) {
      main.scrollTop = main.scrollHeight; // the page opened on a sitting: its end
    } else if (count > seen.current) {
      if (lastAuthor === 'session') {
        const messages = main.querySelectorAll('.msg');
        messages[messages.length - 1]?.scrollIntoView({ block: 'start', behavior: 'smooth' });
      } else {
        main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' });
      }
    }
    seen.current = count;
  }, [count, lastAuthor]);

  // A sitting lost: its line, at the end, in view.
  const lost = Boolean(sitting?.lost);
  useLayoutEffect(() => {
    if (lost) mainRef.current?.scrollTo({ top: mainRef.current.scrollHeight, behavior: 'smooth' });
  }, [lost]);

  const content = (() => {
    if (query.isPending) return <CenterCard title="" testId="inbox.loading" />;
    if (query.isError || !state) {
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
        <Transcript sitting={sitting} tracker={state.tracker} log={inbox.log} />
      </div>
    );
  })();

  return (
    <>
      <InboxHeader sitting={sitting} prefs={prefs} onEnd={() => setEnding(true)} onPref={setPref} />
      <div className="stage inbox">
        <main className="main" ref={mainRef} data-testid="inbox.main">
          {content}
        </main>
        {sitting && !sitting.lost && (
          <Composer
            draft={inbox.draft}
            working={Boolean(sitting.turn)}
            busy={inbox.busy}
            onDraft={inbox.setDraft}
            onSend={() => void inbox.send()}
          />
        )}
        {sitting?.lost && (
          <div className="composer lost-bar" data-testid="inbox.restart">
            <div className="column composer-row">
              <span className="lost-note">The session is gone, and Send with it. A new sitting clears this one.</span>
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
          onCancel={() => setEnding(false)}
          onEnd={() => {
            setEnding(false);
            void inbox.end();
          }}
        />
      )}
      <Toasts toasts={inbox.toasts} />
    </>
  );
}
