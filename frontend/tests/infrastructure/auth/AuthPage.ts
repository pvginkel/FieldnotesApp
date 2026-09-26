/**
 * Page object for authentication-related test helpers.
 * Provides locators and actions for auth gate, top bar, user dropdown, and sidebar.
 */

import type { Page, Locator } from '@playwright/test'

export class AuthPage {
  constructor(private page: Page) {}

  // Auth Gate Locators

  get loadingScreen(): Locator {
    return this.page.locator('[data-testid="auth.gate.loading"]')
  }

  get errorScreen(): Locator {
    return this.page.locator('[data-testid="auth.gate.error"]')
  }

  get retryButton(): Locator {
    return this.page.locator('[data-testid="auth.gate.error.retry"]')
  }

  // Top Bar Locators

  get topBar(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar"]')
  }

  get hamburgerButton(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar.hamburger"]')
  }

  get logo(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar.logo"]')
  }

  get title(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar.title"]')
  }

  get homeLink(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar.home-link"]')
  }

  // User Dropdown Locators

  get userDropdownTrigger(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar.user"]')
  }

  get userName(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar.user.name"]')
  }

  get userDropdownMenu(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar.user.dropdown"]')
  }

  get logoutButton(): Locator {
    return this.page.locator('[data-testid="app-shell.topbar.user.logout"]')
  }

  // Sidebar Locators

  get sidebar(): Locator {
    return this.page.locator('[data-testid="app-shell.sidebar"]')
  }

  get mobileOverlay(): Locator {
    return this.page.locator('[data-testid="app-shell.mobile-overlay"]')
  }

  get mobileOverlayDismiss(): Locator {
    return this.page.locator('[data-testid="app-shell.mobile-overlay.dismiss"]')
  }

  // Actions

  async toggleMenu(): Promise<void> {
    await this.hamburgerButton.click()
  }

  async openUserDropdown(): Promise<void> {
    await this.userDropdownTrigger.click()
  }

  async clickLogout(): Promise<void> {
    await this.logoutButton.click()
  }

  async clickRetry(): Promise<void> {
    await this.retryButton.click()
  }

  async clickHomeLink(): Promise<void> {
    await this.homeLink.click()
  }

  async dismissMobileOverlay(): Promise<void> {
    await this.mobileOverlayDismiss.click({ position: { x: 350, y: 300 } })
  }

  // Wait Helpers

  async waitForLoadingScreen(): Promise<void> {
    await this.loadingScreen.waitFor({ state: 'visible' })
  }

  async waitForErrorScreen(): Promise<void> {
    await this.errorScreen.waitFor({ state: 'visible' })
  }

  async waitForAuthenticated(): Promise<void> {
    await this.userDropdownTrigger.waitFor({ state: 'visible' })
  }

  async waitForDropdownOpen(): Promise<void> {
    await this.userDropdownMenu.waitFor({ state: 'visible' })
  }

  async waitForDropdownClosed(): Promise<void> {
    await this.userDropdownMenu.waitFor({ state: 'hidden' })
  }

  // State Queries

  async getUserName(): Promise<string> {
    return (await this.userName.textContent()) ?? ''
  }

  async isSidebarCollapsed(): Promise<boolean> {
    const state = await this.sidebar.getAttribute('data-state')
    return state === 'collapsed'
  }

  async isMobileMenuOpen(): Promise<boolean> {
    const root = this.page.locator('[data-testid="app-shell.root"]')
    const state = await root.getAttribute('data-mobile-menu-state')
    return state === 'open'
  }
}
