/**
 * UserDropdown component.
 * Displays user name with dropdown menu containing logout option.
 */

import { useState, useRef, useEffect } from 'react'
import { useAuthContext } from '@/contexts/auth-context'

/**
 * Get display name for the user.
 * Falls back to "Unknown User" if name is null, empty, or whitespace-only.
 */
function getDisplayName(name: string | null): string {
  if (name && name.trim()) {
    return name.trim()
  }
  return 'Unknown User'
}

/**
 * UserDropdown component.
 * Shows user name as trigger, dropdown with logout option.
 * Closes on outside click or Escape key.
 */
export function UserDropdown() {
  const { user, logout } = useAuthContext()
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const displayName = getDisplayName(user?.name ?? null)

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && event.target && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  // Close dropdown on escape key
  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('keydown', handleEscape)
      return () => document.removeEventListener('keydown', handleEscape)
    }
  }, [isOpen])

  const handleLogout = () => {
    setIsOpen(false)
    logout()
  }

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Dropdown trigger */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex cursor-pointer items-center gap-1 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors"
        aria-expanded={isOpen}
        aria-haspopup="true"
        data-testid="app-shell.topbar.user"
      >
        <span data-testid="app-shell.topbar.user.name">{displayName}</span>
        <svg
          className={`h-4 w-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>

      {/* Dropdown menu */}
      {isOpen && (
        <div
          className="absolute right-0 top-full z-50 mt-1 w-48 rounded-md border border-border bg-popover py-1 shadow-lg"
          role="menu"
          aria-orientation="vertical"
          data-testid="app-shell.topbar.user.dropdown"
        >
          <button
            type="button"
            onClick={handleLogout}
            className="flex w-full cursor-pointer items-center px-4 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
            role="menuitem"
            data-testid="app-shell.topbar.user.logout"
          >
            <svg
              className="mr-2 h-4 w-4"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
              />
            </svg>
            Logout
          </button>
        </div>
      )}
    </div>
  )
}
