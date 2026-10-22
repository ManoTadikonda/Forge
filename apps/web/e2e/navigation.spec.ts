import { expect, test } from '@playwright/test'
import { navLink } from './helpers'

const PAGES = [
  { link: 'Repositories', heading: 'Repositories' },
  { link: 'Pipelines', heading: 'Pipelines' },
  { link: 'Deployments', heading: 'Deployments' },
  { link: 'Environments', heading: 'Environments & services' },
  { link: 'Metrics', heading: 'Metrics' },
  { link: 'Security & SBOM', heading: 'Security & supply chain' },
  { link: 'Audit log', heading: 'Audit log' },
  { link: 'Settings', heading: 'Settings' },
]

test.describe('navigation', () => {
  test.beforeEach(async ({ page }) => {
    // Session comes from the saved storage state; go straight to the dashboard.
    await page.goto('/')
  })

  for (const { link, heading } of PAGES) {
    test(`${link} loads without an error state`, async ({ page }) => {
      await navLink(page, link).click()
      await expect(page.getByRole('heading', { name: heading })).toBeVisible()
      // No page should render the generic failure card on a healthy stack.
      await expect(
        page.getByText('Could not load this view'),
      ).toHaveCount(0)
    })
  }

  const DEEP_LINKS = [
    { path: '/metrics', heading: 'Metrics' },
    { path: '/security', heading: 'Security & supply chain' },
    { path: '/audit', heading: 'Audit log' },
    { path: '/settings', heading: 'Settings' },
    { path: '/environments', heading: 'Environments & services' },
  ]

  for (const { path, heading } of DEEP_LINKS) {
    test(`${path} works as a direct load, not just a client-side route`, async ({
      page,
    }) => {
      // Loading the URL directly also exercises the dev proxy and the SPA
      // fallback. A path proxied to the API would shadow the app route here
      // while still passing a click-through test.
      await page.goto(path)
      await expect(page.getByRole('heading', { name: heading })).toBeVisible()
    })
  }

  test('an unknown route renders the not-found page', async ({ page }) => {
    await page.goto('/this-route-does-not-exist')
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
  })

  test('the skip link is first in the tab order', async ({ page }) => {
    // Wait for the shell to render: pressing Tab before the app mounts tests the
    // tab order of an empty document.
    await page.waitForSelector('#main')
    await page.keyboard.press('Tab')
    await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  })
})
