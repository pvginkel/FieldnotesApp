import { AppPills } from '@/components/shell/app-pills';
import { ScreenNav } from '@/components/shell/screen-nav';
import { UserMenu } from '@/components/triage/user-menu';
import type { Prefs } from '@/lib/triage/browser-state';

interface InboxHeaderProps {
  prefs: Prefs;
  onPref: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void;
}

export function InboxHeader({ prefs, onPref }: InboxHeaderProps) {
  return (
    <header className="topbar" data-testid="inbox.header">
      <div className="brand"><img className="logo" src="/fieldnotes-alt.svg" alt="" />Fieldnotes</div>
      <ScreenNav />
      <div className="topbar-fill" />
      <AppPills />
      <UserMenu prefs={prefs} onPref={onPref} />
    </header>
  );
}
