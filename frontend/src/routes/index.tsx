import { createFileRoute } from '@tanstack/react-router';
import { TriagePage } from '@/components/triage/triage-page';
import { applyTheme, loadBrowserState } from '@/lib/triage/browser-state';

// The operator's theme before the first render, so the page does not flash the other one.
applyTheme(loadBrowserState().prefs.theme);

// `actioner`: the actioner page, opened by the Actioner pill over whatever the screen would show.
export const Route = createFileRoute('/')({
  validateSearch: (search: Record<string, unknown>): { actioner?: true } =>
    search.actioner === true ? { actioner: true } : {},
  component: TriagePage,
});
