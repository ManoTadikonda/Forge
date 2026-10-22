import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end tests run against a live Forge stack.
 *
 * They do not start the backend: `scripts/run_stack.sh start` must already be
 * running, with FORGE_BOOTSTRAP_ADMIN_EMAIL/PASSWORD set. The tests assert on real
 * API responses, so starting a stubbed server would defeat their purpose.
 */
const BASE_URL = process.env.FORGE_WEB_URL ?? 'http://127.0.0.1:5173'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    // Authenticate once; every other project reuses the saved session.
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], storageState: 'e2e/.auth/admin.json' },
      dependencies: ['setup'],
      testIgnore: [/auth\.setup\.ts/, /auth\.spec\.ts/],
    },
    {
      // The sign-in flow itself must start unauthenticated.
      name: 'auth-flow',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /auth\.spec\.ts/,
    },
  ],
  webServer: process.env.FORGE_WEB_URL
    ? undefined
    : {
        command: 'npm run dev',
        url: BASE_URL,
        reuseExistingServer: true,
        timeout: 60_000,
      },
})
