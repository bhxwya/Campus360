# Frontend verification

Run the app with `uv run uvicorn app.main:app --app-dir backend --port 8000`
and use accounts already provisioned for the development or test environment.
Never add credentials to this document or the frontend.

Every dashboard page renders only what the authenticated API returned. The
markup ships neutral placeholders (an em dash, a "loading" row) and
`js/portal.js` replaces them once a response arrives, so an empty, partial or
failed response can never leave a sample academic record on the page.

## Authentication and authorization

1. Open `/pages/login.html`, submit an empty form, and confirm inline validation
   appears without a network login request.
2. Submit valid credentials once and confirm exactly one `POST /api/v1/auth/login`
   request appears in browser DevTools. The server-selected role should determine
   the destination dashboard.
3. Open a dashboard URL for another role. The `GET /api/v1/auth/me` guard must
   redirect to login instead of rendering protected data.
4. Use Sign Out. Confirm `POST /api/v1/auth/logout` includes `X-CSRF-Token`,
   then verify a protected page redirects to login.
5. Confirm every POST/PATCH/DELETE from the browser carries `X-CSRF-Token`. If the
   `campus360_csrf` cookie is missing, `js/api.js` refuses to send the mutation
   and reports "Security token missing" instead of failing opaquely.

## Administrator workflows

1. As an administrator, open Students, Faculty, Courses, and Notices.
   Verify loading state, populated API rows, empty state, and an actionable API
   error state by temporarily using an invalid server URL in a local browser.
2. Use the primary Create action and verify browser validation prevents blank
   required fields. Submit a valid record only in a disposable test environment.
3. Use Manage to update a record and confirm the table refreshes. On the Courses
   page the lead faculty is chosen from the roster the API returned; when that
   roster cannot be loaded the field falls back to a typed profile id.
4. Confirm a deactivation/archive prompt before accepting it. These API actions
   preserve history; they do not permanently delete records.
5. Search a Students page and navigate Next/Previous. Confirm server responses
   use `search`, `limit`, and `offset` query parameters. The degree-program
   filter posts the API's own `department` parameter.
6. Use Export CSV / Export Roster and confirm the file contains every record the
   endpoint returned, not only the visible page.
7. In Reports, confirm Export Master Report downloads the summary, the
   attendance rows and the enrollment rows as one CSV.

## Faculty and student views

1. As faculty, open Assignments and Marks. Create and edit records only in a
   disposable environment; the API must reject a course not led by that user.
2. Open an assignment's submissions, record a valid score no greater than the
   assignment maximum (the dialog validates against the assignment, not the
   submission snapshot), and confirm the submission changes to `graded`.
3. As a student, verify dashboard, attendance, timetable, assignments, marks,
   and announcements show only the current student's API data.
4. Verify empty results replace sample table rows rather than leaving fabricated
   academic records visible.
5. On the student Marks page, Download Results CSV exports the student's own
   recorded components.

## Session handling, feedback and keyboard access

1. Let a session expire (or revoke it) while a dashboard is open. The next API
   call must redirect exactly once to `/pages/login.html?reason=expired`, and the
   sign-in card must explain that the session ended. A 403 must stay on the page
   as a message - a wrong-role response is not a sign-out.
2. Every create / update / deactivate / archive / enrollment / grading action
   shows a corner confirmation, and a failed save reports inside the open dialog.
   Faculty saves reload the page and the confirmation survives the reload.
3. On any dashboard, the first Tab stop is "Skip to main content", which moves
   focus to `<main id="main-content">`. The active sidebar link carries
   `aria-current="page"`, and the primary navigation has a label.
4. Record dialogs are native `<dialog>` elements: focus is contained, Escape
   closes them, and the submit button is disabled while the request is running,
   so a double click cannot create two records.
5. At 390px the topbar wraps a wide action row onto a second line, the
   announcement filter scrolls inside the bar, and tables scroll inside
   `.table-responsive` instead of widening the page.

## Filters and counts that are real

- The student announcement filter is built from the categories the API actually
  returned and filters the rendered notices client-side; the API has no
  server-side category parameter.
- The student assignment topbar shows the live number of assignments without a
  submission.
- Decorative controls without a data source were removed rather than left
  looking functional: semester/week/day tabs on marks and timetable, the
  unread-notification dots, the topbar search boxes on the admin, faculty and
  student dashboards, the login "remember me" switch and the dead "forgot
  password" link.

## Current API limitations surfaced by the UI

- The admin API does not expose an audit-log endpoint, so the admin dashboard
  explicitly reports audit data as unavailable.
- The admin API has no academic-year dimension, so the reports page carries no
  year switcher, and it exposes no grade histogram or CGPA: the grade-distribution
  chart states that no API source exists and the report footer repeats the
  server's own notes about `average_cgpa` and `faculty_publication_count`.
- The admin API has no bulk-action endpoint, so that toolbar button stays
  disabled with an explanatory tooltip.
- The faculty API supports attendance batch creation, but has no faculty-scoped
  roster or timetable read endpoint. The UI cannot safely construct a valid
  batch without those identifiers, so attendance entry remains unavailable.
- The faculty API has no announcement endpoint and no course-catalogue or
  student-roster read, so the faculty dashboard, attendance and announcement
  screens show an explicit unavailable state. Assignment and mark forms take a
  course/student id and list the ids the API returned for that account.
- Student endpoints are read-only. Assignment submission/upload is not exposed
  by the current backend and remains unavailable, and no official transcript,
  CGPA or academic-year filter exists, so those tiles state the limitation
  instead of showing a number. There is also no semester-history endpoint, so
  the marks page has no semester selector.
