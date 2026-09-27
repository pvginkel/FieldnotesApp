// What the triage screen keeps in the browser, not the store (ruled 2026-09-27): unsaved notes,
// skips, the visit order and the operator's preferences. The rulings are the store's.

import type { Verb } from './queue';

export interface Draft {
  verb: Verb | null;
  note: string;
}

export type Theme = 'system' | 'light' | 'dark';

export interface Prefs {
  theme: Theme;
}

export interface BrowserState {
  drafts: Record<string, Draft>; // typed but not ruled yet
  skipped: Record<string, string>; // id → when it was last skipped
  seen: string[]; // the order cards were seen in, until the next submit
  prefs: Prefs;
}

const STORAGE = 'fieldnotes-triage-v1';

const initial = (): BrowserState => ({
  drafts: {},
  skipped: {},
  seen: [],
  prefs: { theme: 'system' },
});

export function loadBrowserState(): BrowserState {
  const state = initial();
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE) || '{}') as Partial<BrowserState> & { lastWritten?: unknown };
    delete saved.lastWritten; // what an earlier build kept for the empty page
    return { ...state, ...saved, prefs: { theme: saved.prefs?.theme ?? state.prefs.theme } }; // an earlier build kept reportsPlace too
  } catch {
    return state;
  }
}

export function saveBrowserState(state: BrowserState): void {
  localStorage.setItem(STORAGE, JSON.stringify(state));
}

const darkQuery = () => matchMedia('(prefers-color-scheme: dark)');

/** The page's `data-theme`, which app-theme.css reads: the preference, or the system's. */
export function applyTheme(theme: Theme): void {
  const dark = theme === 'dark' || (theme === 'system' && darkQuery().matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

/** Follows the system's theme while the preference is `system`; returns the unsubscribe. */
export function watchSystemTheme(theme: Theme): () => void {
  const query = darkQuery();
  const change = () => applyTheme(theme);
  query.addEventListener('change', change);
  return () => query.removeEventListener('change', change);
}
