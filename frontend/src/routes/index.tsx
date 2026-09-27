import { createFileRoute } from '@tanstack/react-router';
import { TriagePage } from '@/components/triage/triage-page';
import { applyTheme, loadBrowserState } from '@/lib/triage/browser-state';

// The operator's theme before the first render, so the page does not flash the other one.
applyTheme(loadBrowserState().prefs.theme);

export const Route = createFileRoute('/')({
  component: TriagePage,
});
