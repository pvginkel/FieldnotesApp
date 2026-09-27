// The template's user dropdown in the mockup's avatar: the name, logout, and the operator's theme
// preference, which the mockup kept in its own controls.

import { useEffect, useRef, useState } from 'react';
import { LogOut, UserRound } from 'lucide-react';
import { useAuthContext } from '@/contexts/auth-context';
import type { Prefs, Theme } from '@/lib/triage/browser-state';

interface UserMenuProps {
  prefs: Prefs;
  onPref: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void;
}

export function UserMenu({ prefs, onPref }: UserMenuProps) {
  const { user, logout } = useAuthContext();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const name = user?.name?.trim() || 'Unknown User';

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  return (
    <div className="user-menu" ref={ref}>
      <button
        type="button"
        className="user"
        title={name}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen(!open)}
        data-testid="app-shell.topbar.user"
      >
        <UserRound className="icon" aria-hidden="true" />
        <span className="sr-only" data-testid="app-shell.topbar.user.name">{name}</span>
      </button>
      {open && (
        <div className="menu" role="menu" data-testid="app-shell.topbar.user.dropdown">
          <div className="menu-name">{name}</div>
          <label>
            Theme
            <select
              value={prefs.theme}
              onChange={(event) => onPref('theme', event.target.value as Theme)}
              data-testid="triage.prefs.theme"
            >
              <option value="system">system</option>
              <option value="light">light</option>
              <option value="dark">dark</option>
            </select>
          </label>
          <button
            type="button"
            className="btn btn-soft"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              logout();
            }}
            data-testid="app-shell.topbar.user.logout"
          >
            <LogOut className="icon" aria-hidden="true" /> Log out
          </button>
        </div>
      )}
    </div>
  );
}
