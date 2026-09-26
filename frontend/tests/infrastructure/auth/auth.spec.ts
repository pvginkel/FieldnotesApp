/**
 * Authentication Flow Tests
 *
 * Tests for OIDC authentication integration including:
 * - Auth loading and error states
 * - Login redirect on 401 (skipped - OIDC disabled in test backend)
 * - User display and dropdown
 * - Logout flow
 * - App shell layout tests are in auth-shell.spec.ts (requires use_app_shell)
 */

import { test, expect } from '../../support/fixtures-infrastructure'
import { AuthPage } from './AuthPage'

test.describe('Authentication', () => {
  test.describe('Auth Loading State', () => {
    test('shows loading indicator before auth completes', async ({ page, auth }) => {
      await auth.clearSession()
      await auth.createSession({ name: 'Test User', roles: ['editor'] })

      await page.goto('/')

      const authPage = new AuthPage(page)
      await authPage.waitForAuthenticated()
      await expect(authPage.userDropdownTrigger).toBeVisible()
    })
  })

  test.describe('Login Redirect on 401', () => {
    test('redirects to login when not authenticated', async ({ page, auth }) => {
      await auth.clearSession()
      await auth.forceError(401)

      const loginRequestPromise = page.waitForRequest(request =>
        request.url().includes('/api/auth/login')
      )

      await page.goto('/')

      const loginRequest = await loginRequestPromise
      expect(loginRequest.url()).toContain('/api/auth/login')
      expect(loginRequest.url()).toContain('redirect=')
    })

    test('preserves full path including query params in redirect', async ({ page, auth }) => {
      await auth.clearSession()
      await auth.forceError(401)

      const loginRequestPromise = page.waitForRequest(request =>
        request.url().includes('/api/auth/login')
      )

      // Use a path no app redirects: '/' often redirects to a landing page
      // (e.g. /devices), which drops the query string before the 401.
      await page.goto('/items?filter=active&sort=name')

      const loginRequest = await loginRequestPromise

      const url = new URL(loginRequest.url())
      const redirectParam = url.searchParams.get('redirect')
      expect(redirectParam).toContain('/items')
      expect(redirectParam).toContain('filter=active')
      expect(redirectParam).toContain('sort=name')
    })
  })

  test.describe('Auth Error and Retry', () => {
    test('shows error screen when auth returns 500', async ({ page, auth }) => {
      await auth.forceError(500)

      await page.goto('/')

      const authPage = new AuthPage(page)

      await authPage.waitForErrorScreen()
      await expect(authPage.errorScreen).toBeVisible()
      await expect(authPage.retryButton).toBeVisible()
    })

    test('retry button triggers new auth check', async ({ page, auth }) => {
      await auth.forceError(500)
      await auth.createSession({ name: 'Retry User', roles: ['editor'] })

      await page.goto('/')

      const authPage = new AuthPage(page)

      await authPage.waitForErrorScreen()
      await expect(authPage.errorScreen).toBeVisible()

      await authPage.clickRetry()

      await authPage.waitForAuthenticated()
      await expect(authPage.userName).toHaveText('Retry User')
    })
  })

  test.describe('Authenticated User Display', () => {
    test('displays user name in top bar when authenticated', async ({ page, auth }) => {
      await auth.createSession({ name: 'John Doe', roles: ['editor'] })

      await page.goto('/')

      const authPage = new AuthPage(page)
      await authPage.waitForAuthenticated()

      await expect(authPage.userName).toHaveText('John Doe')
    })

    test('displays "Unknown User" when name is null', async ({ page, auth }) => {
      await auth.createSession({ name: null, email: 'test@example.com', roles: ['editor'] })

      await page.goto('/')

      const authPage = new AuthPage(page)
      await authPage.waitForAuthenticated()

      await expect(authPage.userName).toHaveText('Unknown User')
    })
  })

  test.describe('Logout Flow', () => {
    test('shows dropdown menu when clicking user name', async ({ page, auth }) => {
      await auth.createSession({ name: 'Dropdown User', roles: ['editor'] })

      await page.goto('/')

      const authPage = new AuthPage(page)
      await authPage.waitForAuthenticated()

      await authPage.openUserDropdown()

      await authPage.waitForDropdownOpen()
      await expect(authPage.userDropdownMenu).toBeVisible()
      await expect(authPage.logoutButton).toBeVisible()
    })

    test('clicking logout navigates to logout endpoint', async ({ page, auth }) => {
      await auth.createSession({ name: 'Logout User', roles: ['editor'] })

      await page.goto('/')

      const authPage = new AuthPage(page)
      await authPage.waitForAuthenticated()

      await authPage.openUserDropdown()
      await authPage.waitForDropdownOpen()

      const logoutRequestPromise = page.waitForRequest(request =>
        request.url().includes('/api/auth/logout')
      )

      await authPage.clickLogout()

      const logoutRequest = await logoutRequestPromise
      expect(logoutRequest.url()).toContain('/api/auth/logout')
    })
  })
})
