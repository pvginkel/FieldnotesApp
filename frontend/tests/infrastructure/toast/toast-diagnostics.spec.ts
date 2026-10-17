/**
 * Toast diagnostics tests
 *
 * An exception toast swaps its icon for a button that copies the error's
 * diagnostics, where GET /api/ui-setup reports expose_internals. Playwright's
 * backends run under FLASK_ENV=testing, a local stage, which exposes them.
 */

import { test, expect } from '../../support/fixtures'

test.describe('Toast diagnostics', () => {
  test('an exception toast copies its diagnostics', async ({ page, context, backendUrl }) => {
    const response = await page.request.get(`${backendUrl}/api/ui-setup`)
    expect(response.ok()).toBe(true)
    const { expose_internals: exposeInternals } = (await response.json()) as { expose_internals: boolean }
    expect(exposeInternals).toBe(true)

    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.goto('/')

    await page.evaluate(() => {
      window.dispatchEvent(
        new CustomEvent('app:testing:show-exception', {
          detail: { message: 'toast diagnostics integration', error: new Error('toast diagnostics wiring') },
        })
      )
    })

    const toast = page.getByTestId('app-shell.toast.item').filter({ hasText: 'toast diagnostics integration' })
    const copyButton = toast.getByTestId('app-shell.toast.copy-button')
    await expect(copyButton).toBeVisible()

    await copyButton.click()
    await expect(copyButton).toHaveAttribute('data-copy-state', 'success')

    const copied = await page.evaluate(() => navigator.clipboard.readText())
    expect(copied).toContain('[ERROR] toast diagnostics integration')
    expect(copied).toContain('--- Diagnostics ---')
    expect(copied).toContain('Error: toast diagnostics wiring')
  })
})
