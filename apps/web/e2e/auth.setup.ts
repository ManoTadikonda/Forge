import { expect, test as setup } from '@playwright/test'
import { ADMIN_EMAIL, ADMIN_PASSWORD, STORAGE_STATE } from './helpers'

/**
 * Sign in once and save the session for every other test.
 *
 * Logging in per test would be both slow and self-defeating: Forge rate-limits
 * sign-ins, so a suite that authenticates on every test eventually trips its own
 * protection. Reusing storage state exercises the real cookie session instead.
 */
setup('authenticate once', async ({ page }) => {
  if (!ADMIN_PASSWORD) {
    throw new Error(
      'FORGE_BOOTSTRAP_ADMIN_PASSWORD must be set so the e2e suite can sign in. ' +
        'Start the stack with scripts/run_stack.sh, which sets it.',
    )
  }
  await page.goto('/login')
  await page.getByLabel('Email address').fill(ADMIN_EMAIL)
  await page.getByLabel('Password').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await page.context().storageState({ path: STORAGE_STATE })
})
