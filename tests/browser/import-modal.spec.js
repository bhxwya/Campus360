const { test, expect } = require('@playwright/test');
const { loginAsAdmin, expectNoHorizontalOverflow } = require('./helpers');

test.describe('Import Modal States', () => {
  test('Upload state shows no preview table', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/pages/admin/students.html');
    await page.click('button:has-text("Import Students")');
    const dialog = page.locator('dialog.import-dialog');
    await expect(dialog).toBeVisible({ timeout: 5000 });
    // Upload step visible
    await expect(dialog.locator('[data-step="upload"]')).toBeVisible();
    // Preview and results steps hidden
    await expect(dialog.locator('[data-step="preview"]')).toBeHidden();
    await expect(dialog.locator('[data-step="results"]')).toBeHidden();
    // No preview table in upload state
    await expect(dialog.locator('#import-preview-rows')).not.toBeVisible();
    // Cancel button visible, Back/Confirm/Done hidden
    await expect(dialog.locator('[data-cancel]')).toBeVisible();
    await expect(dialog.locator('[data-back-to-upload]')).toBeHidden();
    await expect(dialog.locator('[data-confirm-import]')).toBeHidden();
    await expect(dialog.locator('[data-done-import]')).toBeHidden();
  });

  test('Modal has proper structure', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/pages/admin/students.html');
    await page.click('button:has-text("Import Students")');
    const dialog = page.locator('dialog.import-dialog');
    await expect(dialog).toBeVisible({ timeout: 5000 });
    // Header
    await expect(dialog.locator('.import-modal-header h2')).toHaveText('Import Students');
    // Step indicator
    await expect(dialog.locator('.import-steps-indicator')).toBeVisible();
    // Body
    await expect(dialog.locator('.import-modal-body')).toBeVisible();
    // Footer
    await expect(dialog.locator('.import-modal-footer')).toBeVisible();
  });

  test('No page-wide horizontal overflow at 320px', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    await loginAsAdmin(page);
    await page.goto('/pages/admin/students.html');
    await page.click('button:has-text("Import Students")');
    const dialog = page.locator('dialog.import-dialog');
    await expect(dialog).toBeVisible({ timeout: 5000 });
    await expectNoHorizontalOverflow(page);
  });
});
