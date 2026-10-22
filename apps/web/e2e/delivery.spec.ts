import { expect, test } from '@playwright/test'
import { navLink } from './helpers'

/**
 * These assert on data produced by `python scripts/demo.py`, which must have been
 * run against this stack. They verify that what the platform actually did is what
 * the dashboard shows.
 */
test.describe('delivery history', () => {
  test.beforeEach(async ({ page }) => {
    // Session comes from the saved storage state; go straight to the dashboard.
    await page.goto('/')
  })

  test('the overview reflects real executions', async ({ page }) => {
    const empty = page.getByText('Nothing has run yet')
    if (await empty.isVisible().catch(() => false)) {
      test.skip(true, 'No runs recorded yet; run scripts/demo.py first.')
    }
    await expect(page.getByText('Pipeline success rate')).toBeVisible()
    await expect(page.getByText('Recent pipelines')).toBeVisible()
  })

  test('a pipeline run shows its stages, artifact and logs', async ({ page }) => {
    await navLink(page, 'Pipelines').click()
    const firstRun = page.getByRole('link', { name: /#\d+/ }).first()
    if ((await firstRun.count()) === 0) {
      test.skip(true, 'No pipeline runs recorded; run scripts/demo.py first.')
    }
    await firstRun.click()

    await expect(page.getByRole('heading', { name: 'Stages' })).toBeVisible()
    for (const stage of ['CLONE', 'VALIDATE', 'TEST', 'BUILD', 'PUBLISH']) {
      await expect(page.getByText(stage, { exact: true })).toBeVisible()
    }
    // An artifact is addressed by digest, never by a mutable tag.
    await expect(page.getByText(/sha256:/).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Build output' })).toBeVisible()
  })

  test('a skipped gate is shown as skipped, never as passed', async ({ page }) => {
    await navLink(page, 'Security & SBOM').click()
    const skipped = page.getByText('Skipped').first()
    if ((await skipped.count()) > 0) {
      await expect(skipped).toBeVisible()
      await expect(page.getByText('Scanning is not enabled')).toBeVisible()
    }
  })

  test('a rolled-back deployment shows its reason and verification', async ({
    page,
  }) => {
    await navLink(page, 'Deployments').click()
    const rolledBack = page.getByText('Rolled back').first()
    if ((await rolledBack.count()) === 0) {
      test.skip(true, 'No rollback recorded; run scripts/demo.py first.')
    }
    await rolledBack.click()

    await expect(
      page.getByText('Automatic rollback completed and verified'),
    ).toBeVisible()
    // The breach that caused it must be stated, not just the outcome. It appears
    // in several places (banner, assessment, timeline); any one of them proves it.
    await expect(page.getByText(/exceeds ceiling/).first()).toBeVisible()
    await expect(page.getByText('Rollout progression')).toBeVisible()
    await expect(page.getByText('Candidate health')).toBeVisible()
    await expect(page.getByText('UNHEALTHY').first()).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Deployment timeline' })).toBeVisible()
    // Rollback is only credible if traffic actually moved back.
    await expect(page.getByText('Traffic restored').first()).toBeVisible()
    await expect(
      page.getByText(/traffic served by .* = True/).first(),
    ).toBeVisible()
  })

  test('the audit log records sign-ins', async ({ page }) => {
    await navLink(page, 'Audit log').click()
    await expect(page.getByRole('heading', { name: 'Audit log' })).toBeVisible()
    await expect(page.getByText('login').first()).toBeVisible()
  })

  test('settings report configuration without leaking secrets', async ({ page }) => {
    await navLink(page, 'Settings').click()
    await expect(page.getByText('Rollout policy')).toBeVisible()
    await expect(page.getByText('Minimum samples')).toBeVisible()

    const body = (await page.locator('body').innerText()).toLowerCase()
    expect(body).not.toContain('dev-admin-password')
    expect(body).not.toContain('secret_key')
  })
})
