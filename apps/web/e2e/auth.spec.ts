import { expect, test } from '@playwright/test'
import { ADMIN_EMAIL, signIn } from './helpers'

test.describe('authentication', () => {
  test('unauthenticated visitors are sent to the sign-in page', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Sign in to Forge' })).toBeVisible()
  })

  test('invalid credentials show an error and do not sign in', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('Email address').fill(ADMIN_EMAIL)
    await page.getByLabel('Password').fill('definitely-the-wrong-password')
    await page.getByRole('button', { name: 'Sign in' }).click()

    await expect(page.getByRole('alert')).toContainText(/incorrect/i)
    await expect(page.getByRole('heading', { name: 'Sign in to Forge' })).toBeVisible()
  })

  test('valid credentials reach the dashboard', async ({ page }) => {
    await signIn(page)
    await expect(page.getByText(ADMIN_EMAIL)).toBeVisible()
  })

  test('signing out returns to the sign-in page', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page.getByRole('heading', { name: 'Sign in to Forge' })).toBeVisible()

    // The session is genuinely gone, not just hidden by the router.
    await page.goto('/deployments')
    await expect(page.getByRole('heading', { name: 'Sign in to Forge' })).toBeVisible()
  })

  test('the session survives a page reload', async ({ page }) => {
    await signIn(page)
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  })
})
