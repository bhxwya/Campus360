const { test, expect } = require('@playwright/test');
const { loginAsAdmin, expectNoHorizontalOverflow } = require('./helpers');

test.describe('Campus360 Smoke Tests', () => {
  test('Login page loads', async ({ page }) => {
    await page.goto('/pages/login.html');
    await expect(page).toHaveTitle(/Login/);
    await expect(page.locator('input[name="campus-role"][value="admin"]')).toBeAttached();
    await expect(page.locator('#campus-id')).toBeVisible();
    await expect(page.locator('#password')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('Admin login and redirect', async ({ page }) => {
    await loginAsAdmin(page);
    await expect(page).toHaveURL(/admin\/dashboard\.html/);
  });

  test('Student Directory loads', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/pages/admin/students.html');
    await expect(page.locator('.data-table.student-directory-table')).toBeVisible();
    await expect(page.locator('th').filter({ hasText: 'Roll Number' })).toBeVisible();
    await expect(page.locator('th').filter({ hasText: 'Actions' })).toBeVisible();
  });

  test('Faculty Directory loads', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/pages/admin/faculty.html');
    await expect(page.locator('.data-table.faculty-directory-table')).toBeVisible();
    await expect(page.locator('th').filter({ hasText: 'Department' })).toBeVisible();
  });

  test('No horizontal overflow on Student Directory', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/pages/admin/students.html');
    await expectNoHorizontalOverflow(page);
  });
});
