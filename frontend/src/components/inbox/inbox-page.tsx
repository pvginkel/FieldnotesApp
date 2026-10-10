// The inbox screen: its header, with the app bar (FR-33).

import { useEffect, useState } from 'react';
import {
  applyTheme,
  loadBrowserState,
  saveBrowserState,
  watchSystemTheme,
  type Prefs,
} from '@/lib/triage/browser-state';
import { InboxHeader } from './inbox-header';

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
  const { prefs, setPref } = usePrefs();
  return (
    <>
      <InboxHeader prefs={prefs} onPref={setPref} />
      <div className="stage">
        <main className="main" data-testid="inbox.main" />
      </div>
    </>
  );
}
