/* End-to-end workflows across the three portals.
 *
 * WRITES DATA: run only against a server backed by a dedicated test
 * database (see README "Browser tests"). Every record uses a per-run suffix,
 * so repeated runs never collide. Nothing is deleted - the API has no hard
 * delete by design (records are deactivated or archived).
 */
const { test, expect } = require('@playwright/test');
const { admin, login, loginAsAdmin, trackPageErrors } = require('./helpers');

const RUN = `${Date.now().toString(36)}`;
const PASSWORD = 'E2e-Password-123';
const people = {
  student: { email: `e2e.student.${RUN}@campus360.example.com`, code: `E2E-ST-${RUN}`, name: 'Edith Student' },
  formStudent: { email: `e2e.form.${RUN}@campus360.example.com`, code: `E2E-FS-${RUN}`, name: 'Form Created Student' },
  outsider: { email: `e2e.outsider.${RUN}@campus360.example.com`, code: `E2E-OUT-${RUN}`, name: 'Olive Outsider' },
  faculty: { email: `e2e.faculty.${RUN}@campus360.example.com`, code: `E2E-FAC-${RUN}`, name: 'Frank Faculty' },
};
const COURSE = { code: `E2E${RUN}`.slice(0, 16).toUpperCase(), title: `E2E Course ${RUN}` };

/* Authenticated JSON calls made with the page's own cookies (and CSRF). */
async function api(page, method, path, body) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === 'campus360_csrf');
  const response = await page.request.fetch(`/api/v1${path}`, {
    method,
    data: body,
    headers: csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf.value) } : {},
  });
  const payload = response.headers()['content-type']?.includes('json') ? await response.json() : null;
  return { status: response.status(), body: payload };
}

async function acceptNextConfirm(page) {
  page.once('dialog', (dialog) => dialog.accept());
}

async function readDownload(page, trigger) {
  const [download] = await Promise.all([page.waitForEvent('download'), trigger()]);
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return { name: download.suggestedFilename(), text: Buffer.concat(chunks).toString('utf8') };
}

function csvFile(name, text) {
  return { name, mimeType: 'text/csv', buffer: Buffer.from(text, 'utf8') };
}

test.describe.configure({ mode: 'serial' });

test.describe('Campus360 workflows', () => {
  test('setup: seed accounts, course and enrollments through the admin API', async ({ page }) => {
    await loginAsAdmin(page);
    const faculty = await api(page, 'POST', '/admin/faculty', {
      email: people.faculty.email,
      password: PASSWORD,
      faculty_code: people.faculty.code,
      full_name: people.faculty.name,
      department: 'CSE',
    });
    expect(faculty.status, JSON.stringify(faculty.body)).toBe(201);
    people.faculty.id = faculty.body.id;

    for (const key of ['student', 'outsider']) {
      const created = await api(page, 'POST', '/admin/students', {
        email: people[key].email,
        password: PASSWORD,
        student_code: people[key].code,
        full_name: people[key].name,
        current_semester: 3,
      });
      expect(created.status, JSON.stringify(created.body)).toBe(201);
      people[key].id = created.body.id;
    }

    const course = await api(page, 'POST', '/admin/courses', {
      code: COURSE.code,
      title: COURSE.title,
      credits: 4,
      lead_faculty_id: people.faculty.id,
    });
    expect(course.status, JSON.stringify(course.body)).toBe(201);
    COURSE.id = course.body.id;

    const enrollment = await api(page, 'POST', '/admin/enrollments', {
      student_id: people.student.id,
      course_id: COURSE.id,
    });
    expect(enrollment.status).toBe(201);
  });

  test('login: validation, wrong password, role mismatch and protected pages', async ({ page }) => {
    await page.goto('/pages/admin/students.html');
    // The page guard and the API client may both redirect; poll the final URL.
    await expect(page).toHaveURL(/login\.html\?reason=expired/, { timeout: 10000 });
    await expect(page.locator('#auth-notice')).toContainText('session expired');

    await page.click('button[type="submit"]');
    await expect(page.locator('#campus-id-error')).toContainText('institutional email');
    await expect(page.locator('#password-error')).toBeVisible();

    await page.fill('#campus-id', 'ST-2026-0001');
    await page.fill('#password', 'whatever');
    await page.click('button[type="submit"]');
    await expect(page.locator('#campus-id-error')).toContainText('user@campus.edu');

    await page.click('label:has-text("Student")');
    await page.fill('#campus-id', people.student.email);
    await page.fill('#password', 'not-the-password');
    await page.click('button[type="submit"]');
    await expect(page.locator('#login-error')).toHaveText('Invalid email or password');

    // Valid admin credentials with the Student pill: refused, session revoked.
    await page.fill('#campus-id', admin.email);
    await page.fill('#password', admin.password);
    await page.click('button[type="submit"]');
    await expect(page.locator('#login-error')).toContainText('registered as an Admin');
    await expect(page).toHaveURL(/login\.html/);
    expect((await api(page, 'GET', '/auth/me')).status).toBe(401);

    // A student session cannot open an admin page.
    await login(page, 'student', people.student.email, PASSWORD);
    await page.goto('/pages/admin/dashboard.html');
    await expect(page).toHaveURL(/login\.html\?reason=forbidden/, { timeout: 10000 });
    expect((await api(page, 'GET', '/admin/students')).status).toBe(403);
  });

  test('admin students: create, duplicate field errors, edit, search, details, export, deactivate', async ({ page }) => {
    const errors = trackPageErrors(page);
    await loginAsAdmin(page);
    await page.goto('/pages/admin/students.html');
    await expect(page.locator('button:has-text("Enroll New Student")')).toBeEnabled();

    const fillStudent = async () => {
      await page.click('button:has-text("Enroll New Student")');
      const dialog = page.locator('dialog.api-dialog[open]');
      await dialog.locator('#form-email').fill(people.formStudent.email);
      await dialog.locator('#form-password').fill(PASSWORD);
      await dialog.locator('#form-student_code').fill(people.formStudent.code);
      await dialog.locator('#form-full_name').fill('=cmd form student');
      await dialog.locator('#form-degree_program').selectOption({ index: 1 });
      await dialog.locator('#form-current_semester').selectOption('5');
      await dialog.locator('#form-phone').fill('555-0101');
      await dialog.locator('button[type="submit"]').click();
      return dialog;
    };

    await fillStudent();
    await expect(page.getByText('Student created.')).toBeVisible();
    const stored = await api(page, 'GET', `/admin/students?search=${encodeURIComponent(people.formStudent.code)}`);
    expect(stored.body.total).toBe(1);
    expect(stored.body.items[0].current_semester).toBe(5);
    expect(stored.body.items[0].programme_code).toBeTruthy();
    people.formStudent.id = stored.body.items[0].id;

    // Same email and roll number again: each field gets its own message.
    const duplicate = await fillStudent();
    await expect(duplicate.locator('[data-field-error="email"]')).toContainText('already registered');
    await expect(duplicate.locator('[data-field-error="student_code"]')).toContainText('already assigned');
    await duplicate.locator('[data-cancel]').click();

    // Search, export (formula-like cells are neutralised), then edit the name.
    await page.fill('[data-table-search]', people.formStudent.code);
    const row = page.locator('.data-table tbody tr', { hasText: people.formStudent.code });
    await expect(row).toHaveCount(1);
    const injected = await readDownload(page, () => page.click('[data-export-csv]'));
    expect(injected.name).toBe('campus360-students.csv');
    expect(injected.text).toContain('"\'=Cmd Form Student"');
    await row.locator('[data-action="edit"]').click();
    const edit = page.locator('dialog.api-dialog[open]');
    await edit.locator('#form-full_name').fill('Renamed Form Student');
    await edit.locator('button[type="submit"]').click();
    await expect(page.getByText('Student updated.')).toBeVisible();
    await expect(row).toContainText('Renamed Form Student');
    const reread = await api(page, 'GET', `/admin/students/${people.formStudent.id}`);
    expect(reread.body.full_name).toBe('Renamed Form Student');

    // Read-only details dialog.
    await row.locator('[data-action="details"]').click();
    const details = page.locator('dialog.student-details-dialog');
    await expect(details).toContainText(people.formStudent.code);
    await expect(details).toContainText('555-0101');
    await details.locator('.student-details-close').click();
    await expect(details).toHaveCount(0);

    // The export follows the current search.
    await page.fill('[data-table-search]', people.student.code);
    await expect(page.locator('.data-table tbody tr', { hasText: people.student.code })).toHaveCount(1);
    const exported = await readDownload(page, () => page.click('[data-export-csv]'));
    expect(exported.text).toContain(people.student.code);
    expect(exported.text).not.toContain(people.formStudent.code);
    await page.fill('[data-table-search]', people.formStudent.code);
    await expect(row).toHaveCount(1);

    // Deactivate keeps the row and flips the badge.
    await acceptNextConfirm(page);
    await row.locator('[data-action="deactivate"]').click();
    await expect(page.getByText('Student deactivated.')).toBeVisible();
    await expect(row.locator('.badge')).toHaveText('Inactive');
    expect(errors).toEqual([]);
  });

  test('admin faculty: duplicate code error, filters, filtered export', async ({ page }) => {
    const errors = trackPageErrors(page);
    await loginAsAdmin(page);
    await page.goto('/pages/admin/faculty.html');
    await page.click('button:has-text("Add Faculty Member")');
    const dialog = page.locator('dialog.api-dialog[open]');
    await dialog.locator('#form-email').fill(`e2e.dupfac.${RUN}@campus360.example.com`);
    await dialog.locator('#form-password').fill(PASSWORD);
    await dialog.locator('#form-faculty_code').fill(people.faculty.code);
    await dialog.locator('#form-full_name').fill('Duplicate Code');
    await dialog.locator('button[type="submit"]').click();
    await expect(dialog.locator('[data-field-error="faculty_code"]')).toContainText('already assigned');
    await dialog.locator('[data-cancel]').click();

    await page.fill('#faculty-search', people.faculty.code);
    const row = page.locator('.data-table tbody tr', { hasText: people.faculty.code });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('Computer Science');

    await page.selectOption('#faculty-state', 'inactive');
    await expect(page.locator('.data-table tbody')).toContainText('No faculty members found.');
    const empty = await readDownload(page, () => page.click('[data-export-csv]'));
    expect(empty.text).not.toContain(people.faculty.code);

    await page.selectOption('#faculty-state', 'active');
    await expect(row).toHaveCount(1);
    const exported = await readDownload(page, () => page.click('[data-export-csv]'));
    expect(exported.text).toContain(people.faculty.code);
    expect(errors).toEqual([]);
  });

  test('admin courses and enrollments: create course, enroll, drop, reactivate', async ({ page }) => {
    const errors = trackPageErrors(page);
    await loginAsAdmin(page);
    await page.goto('/pages/admin/courses.html');
    const code = `C${RUN}`.slice(0, 16).toUpperCase();
    await page.click('button:has-text("Create New Course")');
    const dialog = page.locator('dialog.api-dialog[open]');
    await dialog.locator('#form-code').fill(code);
    await dialog.locator('#form-title').fill(`UI Course ${RUN}`);
    await dialog.locator('#form-credits').fill('3');
    await dialog.locator('#form-lead_faculty_id').selectOption(String(people.faculty.id));
    await dialog.locator('button[type="submit"]').click();
    await expect(page.getByText('Course created.')).toBeVisible();
    const courses = await api(page, 'GET', `/admin/courses?search=${code}`);
    expect(courses.body.items[0].lead_faculty_name).toBe(people.faculty.name);

    await page.goto('/pages/admin/students.html');
    await page.click('button:has-text("Manage Enrollments")');
    const enroll = page.locator('dialog.api-dialog[open]');
    await enroll.locator('#enrollment-student').selectOption(String(people.outsider.id));
    await enroll.locator('#enrollment-course').selectOption(String(courses.body.items[0].id));
    await enroll.locator('button[type="submit"]').click();
    await expect(page.getByText('Student enrolled.')).toBeVisible();
    const enrolledRow = enroll.locator('tbody tr', { hasText: `UI Course ${RUN}` });
    await acceptNextConfirm(page);
    await enrolledRow.locator('button:has-text("Drop")').click();
    await expect(enrolledRow.locator('.badge')).toHaveText('dropped');
    await enrolledRow.locator('button:has-text("Reactivate")').click();
    await expect(enrolledRow.locator('.badge')).toHaveText('active');
    expect(errors).toEqual([]);
  });

  test('admin notices: publish, archive, republish; students see active notices only', async ({ page }) => {
    const errors = trackPageErrors(page);
    await loginAsAdmin(page);
    await page.goto('/pages/admin/notices.html');
    const title = `E2E notice ${RUN}`;
    await page.click('button:has-text("Broadcast New Notice")');
    const dialog = page.locator('dialog.api-dialog[open]');
    await dialog.locator('#form-title').fill(title);
    await dialog.locator('#form-body').fill('Body of the end-to-end notice.');
    await dialog.locator('#form-category').selectOption('examination');
    await dialog.locator('#form-audience').selectOption('students');
    await dialog.locator('button[type="submit"]').click();
    await expect(page.getByText('Announcement created.')).toBeVisible();
    const row = page.locator('.data-table tbody tr', { hasText: title });
    await acceptNextConfirm(page);
    await row.locator('[data-action="archive"]').click();
    await expect(row.locator('.badge')).toHaveText('archived');
    await acceptNextConfirm(page);
    await row.locator('[data-action="archive"]').click();
    await expect(row.locator('.badge')).toHaveText('active');
    expect(errors).toEqual([]);
  });

  test('student import: file errors, mixed rows, review and confirm', async ({ page }) => {
    const errors = trackPageErrors(page);
    await loginAsAdmin(page);
    await page.goto('/pages/admin/students.html');
    await page.click('button:has-text("Import Students")');
    const dialog = page.locator('dialog.import-dialog');
    const fileInput = dialog.locator('#import-file');
    await expect(dialog.locator('#import-file-hint')).toHaveText('Awaiting file…');

    await fileInput.setInputFiles(csvFile('bad.csv', 'email,student_code\na@b.com,X1\n'));
    await expect(dialog.locator('[data-form-error]')).toContainText('Missing required column');
    await expect(dialog.locator('[data-continue-import]')).toBeDisabled();

    const header = 'email,student_code,full_name,degree_program,current_semester,phone';
    const good = `imp.ok.${RUN}@campus360.example.com,IMP-OK-${RUN},grace hopper,B.Tech CSE,2,`;
    const bad = `not an email,IMP-BAD-${RUN},Bad Row,,99,`;
    await fileInput.setInputFiles(csvFile('mixed.csv', `${header}\n${good}\n${bad}\n`));
    await expect(dialog.locator('#import-file-hint')).toContainText('1 of 2 rows passed');
    await dialog.locator('[data-continue-import]').click();
    await expect(dialog.locator('#import-summary-valid')).toHaveText('1');
    await expect(dialog.locator('#import-preview-rows')).toContainText('Email is not a valid address.');
    await expect(dialog.locator('#import-preview-rows')).toContainText('Semester must be between');
    await dialog.locator('[data-confirm-import]').click();
    await expect(dialog.locator('#import-results-summary')).toHaveText('Created: 1 · Skipped: 0 · Failed: 0.');
    await dialog.locator('[data-done-import]').click();
    await expect(dialog).toHaveCount(0);

    const created = await api(page, 'GET', `/admin/students?search=IMP-OK-${RUN}`);
    expect(created.body.total).toBe(1);
    expect(created.body.items[0].is_active).toBe(false);
    expect(created.body.items[0].full_name).toBe('Grace Hopper');
    expect(created.body.items[0].degree_program).toBeTruthy();

    // Re-importing the same file: the valid row now exists and is rejected in review.
    await page.click('button:has-text("Import Students")');
    const again = page.locator('dialog.import-dialog');
    await again.locator('#import-file').setInputFiles(csvFile('again.csv', `${header}\n${good}\n`));
    await expect(again.locator('#import-file-hint')).toContainText('0 of 1 rows passed');
    await again.locator('[data-continue-import]').click();
    await expect(again.locator('[data-form-error]')).toContainText('No valid rows');
    await expect(again.locator('#import-preview-rows')).toContainText('already registered');
    await expect(again.locator('[data-confirm-import]')).toBeDisabled();
    await again.locator('[data-back-to-upload]').click();
    await again.locator('[data-cancel]').click();
    expect(errors).toEqual([]);
  });

  test('faculty import: valid row creates an inactive faculty account', async ({ page }) => {
    const errors = trackPageErrors(page);
    await loginAsAdmin(page);
    await page.goto('/pages/admin/faculty.html');
    await page.click('button:has-text("Import Faculty")');
    const dialog = page.locator('dialog.import-dialog');
    await expect(dialog.locator('.import-modal-header h2')).toHaveText('Import Faculty');
    // The downloadable template must itself pass validation (apart from the
    // example address already existing after an earlier run).
    const template = await readDownload(page, () => dialog.locator('[data-download-template]').click());
    expect(template.name).toBe('campus360-faculty-import-template.csv');
    await dialog.locator('#import-file').setInputFiles(csvFile('template.csv', template.text));
    await expect(dialog.locator('#import-file-hint')).toContainText('passed validation');
    const templateCheck = await api(page, 'POST', '/admin/faculty/import', { csv_content: template.text.replace(/^\ufeff/, '') });
    for (const error of templateCheck.body.rows[0].errors) expect(error).toMatch(/already (registered|assigned)/);

    const csv = `email,faculty_code,full_name,department,designation\nimp.fac.${RUN}@campus360.example.com,IMP-FAC-${RUN},Alan Kay,Computer Science & Engineering,Professor\n`;
    await dialog.locator('[data-file-remove]').click();
    await dialog.locator('#import-file').setInputFiles(csvFile('faculty.csv', csv));
    await dialog.locator('[data-continue-import]').click();
    await dialog.locator('[data-confirm-import]').click();
    await expect(dialog.locator('#import-results-summary')).toHaveText('Created: 1 · Skipped: 0 · Failed: 0.');
    const created = await api(page, 'GET', `/admin/faculty?search=IMP-FAC-${RUN}`);
    expect(created.body.items[0]).toMatchObject({ is_active: false, role: 'faculty', department_code: 'CSE' });
    expect(errors).toEqual([]);
  });

  test('faculty portal: assignment due date keeps local time; marks require enrollment', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'faculty', people.faculty.email, PASSWORD);

    await page.goto('/pages/faculty/assignments.html');
    await page.click('button:has-text("Create New Assignment")');
    const dialog = page.locator('dialog.api-dialog[open]');
    await dialog.locator('#faculty-course_id').fill(String(COURSE.id));
    await dialog.locator('#faculty-title').fill(`Essay ${RUN}`);
    await dialog.locator('#faculty-due_at').fill('2026-12-01T10:30');
    await dialog.locator('#faculty-max_score').fill('50');
    await dialog.locator('button[type="submit"]').click();
    await expect(page.getByText('Assignment created.')).toBeVisible();

    const assignments = await api(page, 'GET', `/faculty/assignments?course_id=${COURSE.id}`);
    const assignment = assignments.body.find((item) => item.title === `Essay ${RUN}`);
    const expected = await page.evaluate(() => new Date('2026-12-01T10:30').toISOString());
    expect(new Date(assignment.due_at).toISOString()).toBe(expected);

    await page.locator(`[data-assignment-id="${assignment.id}"][data-faculty-action="edit"]`).click();
    await expect(page.locator('dialog.api-dialog[open] #faculty-due_at')).toHaveValue('2026-12-01T10:30');
    await page.locator('dialog.api-dialog[open] [data-cancel]').click();

    await page.goto('/pages/faculty/marks.html');
    const record = async (studentId) => {
      await page.click('button:has-text("Publish Marks")');
      const form = page.locator('dialog.api-dialog[open]');
      await form.locator('#faculty-student_id').fill(String(studentId));
      await form.locator('#faculty-course_id').fill(String(COURSE.id));
      await form.locator('#faculty-component').selectOption('midterm');
      await form.locator('#faculty-score').fill('42');
      await form.locator('#faculty-max_score').fill('50');
      await form.locator('button[type="submit"]').click();
      return form;
    };
    const refused = await record(people.outsider.id);
    await expect(refused.locator('[data-form-error]')).toContainText('not enrolled');
    await refused.locator('[data-cancel]').click();

    await record(people.student.id);
    await expect(page.getByText('Mark recorded.')).toBeVisible();
    await expect(page.locator('.data-table tbody')).toContainText('84%');
    expect(errors).toEqual([]);
  });

  test('student portal: every page renders live data without errors', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'student', people.student.email, PASSWORD);
    await expect(page.locator('.welcome-banner h2')).toHaveText(`Welcome, ${people.student.name}.`);
    await expect(page.locator('.stats-grid')).toContainText('Registered credits');

    await page.goto('/pages/student/marks-results.html');
    await expect(page.locator('.data-table tbody')).toContainText(COURSE.code);
    const results = await readDownload(page, () => page.click('[data-export-csv]'));
    expect(results.text).toContain('midterm');

    await page.goto('/pages/student/assignments.html');
    await expect(page.locator('.data-table tbody')).toContainText(`Essay ${RUN}`);

    await page.goto('/pages/student/announcements.html');
    await expect(page.locator('.announcement-list')).toContainText(`E2E notice ${RUN}`);

    for (const name of ['attendance', 'timetable', 'academic-records']) {
      await page.goto(`/pages/student/${name}.html`);
      await expect(page.locator('#api-page-message[data-kind="error"]')).toHaveCount(0);
    }
    expect(errors).toEqual([]);
  });

  test('every portal page loads without JavaScript or API errors', async ({ page }) => {
    const errors = trackPageErrors(page);
    const failures = [];
    page.on('response', (response) => {
      if (response.url().includes('/api/') && response.status() >= 400) {
        failures.push(`${response.status()} ${response.url()}`);
      }
    });
    const pages = {
      admin: ['dashboard', 'students', 'faculty', 'courses', 'notices', 'reports'],
      faculty: ['dashboard', 'attendance', 'assignments', 'marks', 'announcements'],
      student: ['dashboard', 'attendance', 'timetable', 'assignments', 'marks-results', 'announcements', 'academic-records'],
    };
    const accounts = {
      admin: [admin.email, admin.password],
      faculty: [people.faculty.email, PASSWORD],
      student: [people.student.email, PASSWORD],
    };
    for (const [role, names] of Object.entries(pages)) {
      await page.context().clearCookies();
      await login(page, role, ...accounts[role]);
      for (const name of names) {
        await page.goto(`/pages/${role}/${name}.html`);
        await page.waitForLoadState('networkidle');
        await expect(page).toHaveURL(new RegExp(`${role}/${name}\\.html`));
      }
    }
    expect(errors).toEqual([]);
    expect(failures).toEqual([]);
  });

  test('reports: figures match the API and the master export downloads', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/pages/admin/reports.html');
    const summary = (await api(page, 'GET', '/admin/reports/summary')).body;
    await expect(page.locator('.stats-grid')).toContainText(String(summary.total_enrollments));
    const report = await readDownload(page, () => page.click('[data-report-export]'));
    expect(report.text).toContain('Campus360 master report');
    expect(report.text).toContain(COURSE.code);
  });

  test('sign out works, including after the session already ended', async ({ page }) => {
    await loginAsAdmin(page);
    await page.click('.logout-link');
    await page.waitForURL(/login\.html$/);
    expect((await api(page, 'GET', '/auth/me')).status).toBe(401);

    await loginAsAdmin(page);
    await page.context().clearCookies({ name: 'campus360_session' });
    await page.click('.logout-link');
    await page.waitForURL(/login\.html$/);
  });

  test('activation page: missing and invalid tokens fail safely', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/pages/activate.html');
    await expect(page.locator('#activate-general-error')).toContainText('No activation token');

    await page.goto('/pages/activate.html?token=definitely-not-valid');
    await page.fill('#activate-password', 'BrandNewPass123');
    await page.fill('#activate-password-confirm', 'BrandNewPass123');
    await page.click('#activate-submit');
    // Repeated runs within 15 minutes may meet the activation throttle instead.
    await expect(page.locator('#activate-general-error')).toContainText(/Invalid or expired activation link|Too many activation attempts/);
    expect(page.url()).not.toContain('BrandNewPass123');

    await page.click('#activate-cancel');
    await page.waitForURL(/\/pages\/login\.html$/);
    await expect(page.locator('#login-form')).toBeVisible();
    expect(errors).toEqual([]);
  });
});
