import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';
import { config } from 'dotenv';

const testEnvPath = path.resolve(process.cwd(), '.env.test');
if (fs.existsSync(testEnvPath)) {
  config({ path: testEnvPath, quiet: true });
}

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 2,
  // Exclude @slow tests by default unless INCLUDE_SLOW_TESTS=true
  grep: process.env.INCLUDE_SLOW_TESTS ? undefined : /^(?!.*@slow)/,
  reporter: [
    ['list'],
    ['html', {
      outputFolder: 'playwright-report',
      open: process.env.CI || process.env.CLAUDECODE || process.env.CODEX_MANAGED_BY_NPM ? 'never' : 'on-failure'
    }]
  ],

  use: {
    headless: true,
    viewport: { width: 1280, height: 720 },
    ignoreHTTPSErrors: true,
    video: process.env.PLAYWRIGHT_RETAIN_VIDEO ? 'on' : 'retain-on-failure',
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },

  expect: {
    timeout: 10000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  outputDir: 'test-results/',

  globalSetup: './tests/support/global-setup.ts',
});
