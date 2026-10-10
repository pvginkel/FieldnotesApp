import { AppPills } from '@/components/shell/app-pills';
import { ScreenNav } from '@/components/shell/screen-nav';
import { UserMenu } from '@/components/triage/user-menu';
import type { Sitting } from '@/hooks/use-inbox';
import type { Prefs } from '@/lib/triage/browser-state';

interface InboxHeaderProps {
  sitting: Sitting | null;
  prefs: Prefs;
  onEnd: () => void;
  onPref: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void;
}

export function InboxHeader({ sitting, prefs, onEnd, onPref }: InboxHeaderProps) {
  return (
    <header className="topbar" data-testid="inbox.header">
      <div className="brand"><img className="logo" src="/fieldnotes-alt.svg" alt="" />Fieldnotes</div>
      <ScreenNav />
      <div className="topbar-fill" />
      <AppPills />
      {sitting && (
        <button className="btn btn-primary" type="button" onClick={onEnd} data-testid="inbox.header.end">
          End
        </button>
      )}
      <UserMenu prefs={prefs} onPref={onPref} />
    </header>
  );
}
