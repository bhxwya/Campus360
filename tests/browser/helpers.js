/* Shared helpers for the Playwright browser suite.
 *
 * Credentials come from the environment so no password lives in the
 * repository:
 *   CAMPUS360_E2E_ADMIN_EMAIL / CAMPUS360_E2E_ADMIN_PASSWORD  (required)
 *   CAMPUS360_E2E_BASE_URL   (optional, default http://127.0.0.1:8099)
 * Point the suite at a server that uses a DEDICATED test database: the
 * workflow spec creates students, faculty, courses and notices.
 */
const { expect } = require('@playwright/test');

function requiredEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set. See the "Browser tests" section of README.md.`);
  }
  return value;
}

const admin = {
  get email() { return requiredEnv('CAMPUS360_E2E_ADMIN_EMAIL'); },
  get password() { return requiredEnv('CAMPUS360_E2E_ADMIN_PASSWORD'); },
};

async function login(page, role, email, password) {
  await page.goto('/pages/login.html');
  await page.click(`label:has-text("${role[0].toUpperCase()}${role.slice(1)}")`);
  await page.fill('#campus-id', email);
  await page.fill('#password', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(new RegExp(`${role}/dashboard\\.html`), { timeout: 10000 });
}

async function loginAsAdmin(page) {
  await login(page, 'admin', admin.email, admin.password);
}

/* Collect uncaught page errors and console.error output so a spec can assert
   that a workflow ran without JavaScript failures. The browser's own
   "Failed to load resource" lines are skipped: an expected 409/422 logs one,
   and so do the external web fonts when the machine is offline. Specs that
   care about HTTP failures watch responses instead. */
function trackPageErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) {
      errors.push(`console: ${message.text()}`);
    }
  });
  return errors;
}

async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => document.body.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
}

module.exports = { admin, login, loginAsAdmin, trackPageErrors, expectNoHorizontalOverflow };
