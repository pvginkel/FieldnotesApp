/* eslint-disable react-refresh/only-export-components -- the tab-preference helpers and useRestoreTab belong with RoutedTabs */
import { Link, useLocation, useNavigate } from '@tanstack/react-router'
import { type ReactNode, useEffect } from 'react'

/**
 * Descriptor for a single tab in a `RoutedTabs` bar.
 *
 * Each tab maps to a TanStack Router `<Link>` and optionally participates
 * in localStorage-based preference persistence.
 */
export interface RoutedTabDefinition {
  /** Route path template for TanStack Router `<Link>` `to` prop (e.g. `/things/$thingId/edit`). */
  to: string
  /** Route params passed to the `<Link>` (e.g. `{ thingId: '42' }`). */
  params?: Record<string, string>
  /**
   * Slug written to localStorage when this tab is active.
   * Also used as the suffix for path matching (appended to `basePath`).
   */
  value: string
  /** Tab label — any ReactNode, enabling badge composition by consumers. */
  label: ReactNode
  /** Explicit test ID. If omitted, derived as `${testIdPrefix}.tab.${value}`. */
  testId?: string
}

/**
 * Props for the `RoutedTabs` component.
 */
interface RoutedTabsProps {
  /** Array of tab definitions to render. */
  tabs: RoutedTabDefinition[]
  /**
   * Prefix used to derive `data-testid` attributes:
   * - Nav container: `${testIdPrefix}.tabs`
   * - Each tab link: `${testIdPrefix}.tab.${value}` (unless `testId` is set on the definition)
   */
  testIdPrefix: string
  /**
   * localStorage key for persisting the active tab.
   * When provided, the component writes the active tab's `value` to localStorage
   * on every pathname change.
   */
  storageKey?: string
  /**
   * Base path prefix used for two purposes:
   * 1. Guard: only persist the tab when `location.pathname` starts with this prefix.
   * 2. Match: determine the active tab by checking `pathname.startsWith(basePath + definition.value)`.
   *
   * Must include a trailing slash (e.g. `/things/42/`).
   */
  basePath?: string
}

/**
 * URL-driven tab bar built on TanStack Router `<Link>` components.
 *
 * Renders a `<nav>` with one link per tab definition. Active state is
 * determined by the router (via `activeProps`), and an underline indicator
 * is shown via CSS. Optionally persists the active tab to localStorage.
 *
 * @example
 * ```tsx
 * <RoutedTabs
 *   tabs={[
 *     { to: '/things/$id/edit', params: { id }, value: 'edit', label: 'Edit' },
 *     { to: '/things/$id/logs', params: { id }, value: 'logs', label: 'Logs' },
 *   ]}
 *   testIdPrefix="things.detail"
 *   storageKey="my-app-thing-tab"
 *   basePath={`/things/${id}/`}
 * />
 * ```
 */
export function RoutedTabs({ tabs, testIdPrefix, storageKey, basePath }: RoutedTabsProps) {
  const location = useLocation()

  // Persist the active tab to localStorage whenever the pathname changes.
  // The basePath guard ensures we don't clobber the preference when navigating
  // away from the tabbed view entirely.
  useEffect(() => {
    if (!storageKey || !basePath) return
    if (!location.pathname.startsWith(basePath)) return

    for (const tab of tabs) {
      if (location.pathname.startsWith(basePath + tab.value)) {
        setTabPreference(storageKey, tab.value)
        break
      }
    }
  }, [location.pathname, storageKey, basePath, tabs])

  return (
    <nav className="flex gap-0 px-4" data-testid={`${testIdPrefix}.tabs`}>
      {tabs.map((tab) => (
        <Link
          key={tab.value}
          to={tab.to}
          params={tab.params}
          className="relative px-4 py-2.5 text-sm font-medium transition-colors text-muted-foreground hover:text-foreground [&.active]:text-foreground"
          activeProps={{
            className: 'active',
            'aria-current': 'page',
          }}
          data-testid={tab.testId ?? `${testIdPrefix}.tab.${tab.value}`}
          onClick={() => {
            if (storageKey) setTabPreference(storageKey, tab.value)
          }}
        >
          {tab.label}
          {/* Active underline indicator rendered via CSS */}
          <span className="absolute inset-x-0 bottom-0 h-0.5 bg-transparent [.active>&]:bg-primary" />
        </Link>
      ))}
    </nav>
  )
}

// ---------------------------------------------------------------------------
// Persistence utilities
// ---------------------------------------------------------------------------

/**
 * Read the persisted tab preference from localStorage.
 *
 * Returns `defaultValue` if the stored value is missing, not in `validValues`,
 * or if localStorage is unavailable.
 *
 * @param storageKey - localStorage key to read from.
 * @param validValues - Allowed tab value strings.
 * @param defaultValue - Fallback when no valid preference is stored.
 */
export function getTabPreference(
  storageKey: string,
  validValues: string[],
  defaultValue: string,
): string {
  try {
    const stored = localStorage.getItem(storageKey)
    if (stored && validValues.includes(stored)) {
      return stored
    }
    return defaultValue
  } catch {
    return defaultValue
  }
}

/**
 * Write a tab preference to localStorage.
 *
 * Silently fails if localStorage is unavailable (e.g. private browsing,
 * quota exceeded).
 *
 * @param storageKey - localStorage key to write to.
 * @param value - Tab value string to persist.
 */
export function setTabPreference(storageKey: string, value: string): void {
  try {
    localStorage.setItem(storageKey, value)
  } catch {
    // Silently fail if localStorage is unavailable
  }
}

// ---------------------------------------------------------------------------
// Index-route restore hook
// ---------------------------------------------------------------------------

/**
 * Options for the `useRestoreTab` hook.
 */
interface UseRestoreTabOptions {
  /** localStorage key to read the persisted preference from. */
  storageKey: string
  /** Allowed tab value strings for validation. */
  validValues: string[]
  /** Fallback tab value when no valid preference is stored. */
  defaultValue: string
  /**
   * Maps a tab value to a TanStack Router navigation descriptor.
   * Called once with the resolved tab value.
   */
  toPath: (tab: string) => { to: string; params?: Record<string, string> }
}

/**
 * Hook for index-route redirect to the user's last-used tab.
 *
 * Reads the persisted preference from localStorage, validates it, and
 * navigates with `replace: true` so the index route is not added to
 * browser history.
 *
 * The consumer component should return `null` as its JSX — this hook
 * is a pure side-effect.
 *
 * @example
 * ```tsx
 * function RedirectToPreferredTab() {
 *   const { id } = Route.useParams()
 *   useRestoreTab({
 *     storageKey: 'my-tab-key',
 *     validValues: ['edit', 'logs'],
 *     defaultValue: 'edit',
 *     toPath: (tab) => ({ to: `/things/$id/${tab}`, params: { id } }),
 *   })
 *   return null
 * }
 * ```
 */
export function useRestoreTab({ storageKey, validValues, defaultValue, toPath }: UseRestoreTabOptions): void {
  const navigate = useNavigate()
  const tab = getTabPreference(storageKey, validValues, defaultValue)
  const destination = toPath(tab)

  // Serialize params for a stable dependency — toPath returns a new object
  // each render, so referential equality on destination.params would cause
  // the effect to re-fire on every render.
  const paramsKey = destination.params ? JSON.stringify(destination.params) : ''

  useEffect(() => {
    navigate({
      to: destination.to,
      params: destination.params,
      replace: true,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- paramsKey is a stable serialization of destination.params
  }, [navigate, destination.to, paramsKey])
}
