const { test, expect } = require('@playwright/test');
const { loginAsAdmin, expectNoHorizontalOverflow } = require('./helpers');

const VIEWPORTS = [
  { width: 320, height: 800 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];

for (const vp of VIEWPORTS) {
  test.describe(`Responsive ${vp.width}x${vp.height}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test(`Student Directory - no horizontal overflow at ${vp.width}px`, async ({ page }) => {
      await loginAsAdmin(page);
      await page.goto('/pages/admin/students.html');
      await expectNoHorizontalOverflow(page);
      await expect(page.locator('.table-responsive')).toBeVisible();
    });

    test(`Faculty Directory - no horizontal overflow at ${vp.width}px`, async ({ page }) => {
      await loginAsAdmin(page);
      await page.goto('/pages/admin/faculty.html');
      await expectNoHorizontalOverflow(page);
      await expect(page.locator('.table-responsive')).toBeVisible();
    });
  });
}
