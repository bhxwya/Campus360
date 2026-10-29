const { test, expect } = require('@playwright/test');
const { loginAsAdmin } = require('./helpers');

test.describe('Faculty Department Dropdown', () => {
  test('Faculty create form has department dropdown', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/pages/admin/faculty.html');
    // Click "Add Faculty" button
    await page.click('button:has-text("Add Faculty Member")');
    // Check that a select with department options exists
    const deptSelect = page.locator('select[name="department"]');
    await expect(deptSelect).toBeVisible({ timeout: 5000 });
    // Check that it has options from the catalogue
    const options = await deptSelect.locator('option').allTextContents();
    expect(options.some(o => o.includes('Computer Science'))).toBe(true);
  });
});
