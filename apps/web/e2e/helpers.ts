import type { Locator, Page } from '@playwright/test'
import { expect } from '@playwright/test'

export const ADMIN_EMAIL = process.env.FORGE_BOOTSTRAP_ADMIN_EMAIL ?? 'admin@forge.local'
export const ADMIN_PASSWORD = process.env.FORGE_BOOTSTRAP_ADMIN_PASSWORD ?? ''
export const STORAGE_STATE = 'e2e/.auth/admin.json'

/**
 * Locate a sidebar navigation link.
 *
 * Nav links include a live count badge ("Deployments 2"), so their accessible
 * name is not a fixed string; match the label as a prefix instead.
 */
export function navLink(page: Page, label: string): Locator {
  return page.getByRole('navigation', { name: 'Main' }).getByRole('link', {
    name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`),
  })
}

export async function signIn(page: Page): Promise<void> {
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
}
