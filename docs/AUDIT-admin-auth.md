# Campus360 — Admin Dashboard & Authentication Audit

**Scope:** `pages/admin/*.html`, `js/auth.js`, `js/script.js`, and the FastAPI backend
(`backend/app/api/{admin,auth,deps}.py`, `backend/app/services/{admin,auth}.py`,
`backend/app/schemas/{admin,auth}.py`, `backend/app/models/*`).

**Status:** audit only — no source files changed, no database mutated.
**Baseline:** `238 passed` (backend pytest, `campus360_test` DB), `ruff` clean.

---

## 1. Executive summary

The backend is in good shape. Almost every capability the admin UI needs already
exists, is role-guarded server-side, and is CSRF-protected. The gap is
**entirely on the frontend**: the admin pages ship hard-coded numbers and
non-functional controls, and nothing in `js/` ever calls the API.

Three findings dominate the priority order:

| # | Finding | Impact | Severity |
|---|---------|--------|----------|
| A | Login submit handler is registered *inside* another submit handler (`js/auth.js:332`) | First submit works; every later submit fires N overlapping `fetch` calls | **P0** |
| B | `DEMO_LOGIN_KEY` is used but never declared (`js/auth.js:108,127,136`) | `ReferenceError` on every read/write; dead demo-session code | **P0** |
| C | `csrfToken = readCsrfCookie()` at `js/auth.js:353` assigns to an undeclared identifier | Sloppy-mode implicit global; silently shared across the page | **P1** |

Two more correctness issues worth calling out:

* **D — role pills are advisory only.** The login form asks for Student/Faculty/Admin
  and the backend returns the *real* role. The current code uses the server role for
  routing (correct), but never checks that the chosen pill matches it. A user can
  pick "Student" and still land on the admin dashboard. The UI should either
  derive the pills from the server or warn on mismatch.
* **E — `data-table-search` filters a static table.** `js/script.js:158` binds a
  client-side filter to `pages/admin/students.html`, whose rows are hard-coded.
  Once rows come from the API, this must become a server-side `?search=` query
  (the endpoint already supports it), or it will silently filter a single page.

### What already works (do not rebuild)

* Session auth: `POST /api/v1/auth/login` → HttpOnly `campus360_session` +
  readable `campus360_csrf` cookie; `POST /api/v1/auth/logout` (CSRF-guarded);
  `GET /api/v1/auth/me` (safe GET, no CSRF).
* Authorization: `require_admin` / `require_faculty` / `require_student` on every
  route; role mismatch is enforced by the server, not the frontend.
* CSRF: `require_csrf` validates Origin/Referer **and** a synchronizer token
  hashed against the session row (constant-time compare).
* Admin CRUD for students, faculty, courses, announcements (+ publish/archive),
  and enrolments — all with pagination, `total`, and soft-deactivation on delete.
* Reporting: `/admin/reports/summary`, `/admin/reports/enrollments`,
  `/admin/reports/attendance`.
* The protected-page guard in `js/auth.js:400` already calls `/auth/me` and
  redirects on role mismatch — the right pattern; it just needs extending to
  session expiry.

---

## 2. Endpoint inventory (the API the frontend must target)

Base prefix `/api/v1`. All admin routes are `require_admin`; all unsafe methods
are additionally `require_csrf`.

### Auth

| Method | Path | CSRF | Notes |
|---|---|---|---|
| POST | `/auth/login` | no | Body `{email, password}`. `LoginRequest.email` is `EmailStr` — a Campus ID that is not an email address will be rejected with 422. |
| POST | `/auth/logout` | **yes** | Needs `X-CSRF-Token` header. Returns 204. |
| GET | `/auth/me` | no | Returns `{id, email, role}`. |

### Admin — students / faculty / courses

| Method | Path | Query params |
|---|---|---|
| GET | `/admin/students` | `search`, `department`, `is_active`, `limit` (≤100), `offset` |
| POST | `/admin/students` | — |
| GET/PATCH/DELETE | `/admin/students/{id}` | DELETE = **deactivate**, not destroy |
| GET | `/admin/faculty` | `search`, `department`, `is_active`, `limit`, `offset` |
| POST/GET/PATCH/DELETE | `/admin/faculty[/{id}]` | DELETE = deactivate |
| GET | `/admin/courses` | `search`, `department`, `is_active`, `limit`, `offset` |
| POST/GET/PATCH/DELETE | `/admin/courses[/{id}]` | DELETE = deactivate |

`department` on students filters on `degree_program` (there is no separate
department column on `StudentProfile`).

### Admin — enrolments / announcements / reports

| Method | Path | Query params |
|---|---|---|
| GET | `/admin/enrollments` | `student_id`, `course_id`, `status`, `limit`, `offset`. Returns a bare `EnrollmentResponse[]` — no `{items,total}` envelope. |
| POST | `/admin/enrollments` | — |
| PATCH | `/admin/enrollments/{id}/status` | `status` is a **query param** (`?status=`), not a body field. No DELETE route. |
| GET | `/admin/announcements` | `status`, `category`, `audience`, `limit`, `offset` |
| POST | `/admin/announcements` | — |
| GET/PATCH | `/admin/announcements/{id}` | — |
| POST | `/admin/announcements/{id}/publish` | CSRF |
| POST | `/admin/announcements/{id}/archive` | CSRF |
| GET | `/admin/reports/summary` | — |
| GET | `/admin/reports/enrollments` | — |
| GET | `/admin/reports/attendance` | — |

There is **no** `/admin/reports/performance` route; `pass_percentage` is a field
on `/admin/reports/summary`.

### `ReportSummary` — the live dashboard metrics

```
total_students, active_students, total_faculty, active_faculty,
total_courses, active_courses, active_sessions, total_enrollments,
institution_attendance_rate, pass_percentage,
average_cgpa = None, faculty_publication_count = None, notes[]
```

Two fields are **always `None` by design** — the schema has no grade scale and
no publications table. The backend returns `notes[]` explaining each gap so the
frontend can render a reason instead of a fake number.

---
## 3. Control-by-control audit matrix

Legend — **State**: `LITERAL` = hard-coded value; `STATIC` = fixed markup set;
`DEAD` = no handler at all; `LIVE` = already functional.
**Verdict** = target state and the endpoint that provides it.

### 3.1 `pages/admin/dashboard.html`

| # | Control | Current state | Verdict | Target / endpoint |
|---|---|---|---|---|
| D1 | Stat "Total Enrolled Scholars" = `3,842` | LITERAL | P0 | `GET /admin/reports/summary` → `total_students` |
| D2 | Stat "Faculty Members" = `218` | LITERAL | P0 | `reports/summary` → `total_faculty` / `active_faculty` |
| D3 | Stat "Active Academic Courses" = `146` | LITERAL | P0 | `reports/summary` → `active_courses` |
| D4 | Stat "Platform Active Sessions" = `1,420` | LITERAL | P0 | `reports/summary` → `active_sessions` |
| D5 | Stat trend "↑ 4.8% enrollment growth" | LITERAL | P0 | **No growth data exists.** Drop the trend line, or compute a period-over-period delta from two snapshots. |
| D6 | Department KPI rows (CSE 1,120 • 42 Faculty …) | LITERAL ×5 | P1 | `GET /admin/reports/enrollments` → `by_department` + per-department faculty count |
| D7 | "Audit Activity Trail" (3 fake entries) | STATIC | P1 | **No audit-log model exists.** Use real recent activity from `GET /admin/announcements` and/or recent students/faculty. Do not invent an audit-log endpoint now. |
| D8 | Global search input (topbar) | DEAD | P2 | Wire to `?search=` across students/faculty/courses, or scope to current page |
| D9 | Sidebar nav links | LIVE (static hrefs) | — | Keep; add active-state on non-dashboard pages |
| D10 | "Sign Out" (`.logout-link`) | LIVE-ish | P0 | Already calls `POST /auth/logout` with `X-CSRF-Token`; see X6 for its failure mode |
| D11 | Chart visuals | STATIC | P1 | Bind to `reports/enrollments` + `reports/attendance`; `admin.css` reserves `.chart-card`, `.legend-list` |

### 3.2 `pages/admin/students.html`

| # | Control | Current state | Verdict | Target / endpoint |
|---|---|---|---|---|
| S1 | Table body (5 hard-coded rows) | LITERAL | P0 | `GET /admin/students?limit&offset` → `items[]`, `total` |
| S2 | Search box (`data-table-search`) | DEAD (filters static rows only) | P0 | **Server-side**: `GET /admin/students?search=`. Keep the client filter only as instant-prefix convenience, or remove it — do not let it imply whole-table search. |
| S3 | "Add Student" primary button | DEAD (`disabled`) | P0 | `POST /admin/students` — needs a create form/modal (none in markup) |
| S4 | Department filter `<select>` | DEAD (`disabled`) | P0 | `GET /admin/students?department=` — **populate options from live data**, not the hard-coded list |
| S5 | Semester filter `<select>` | DEAD (`disabled`) | P2 | No backend semester filter on the students list. Drop it, or add the query param to the endpoint. |
| S6 | "Export CSV" button | DEAD (`disabled`) | P1 | Client-side CSV from the fetched page; no endpoint exists |
| S7 | "Bulk Actions" button | DEAD (`disabled`) | P2 | No bulk endpoint. Drop it or scope to per-row actions |
| S8 | Per-row "Manage" buttons (×5) | DEAD (`disabled`) | P0 | `GET/PATCH/DELETE /admin/students/{id}` — edit form + deactivate |
| S9 | Row columns | LITERAL | P0 | Map to `StudentResponse`: `student_code`, `full_name`, `degree_program`, `current_semester`, `enrolled_courses`, `email`, `is_active` |

### 3.3 `pages/admin/faculty.html`

| # | Control | Current state | Verdict | Target / endpoint |
|---|---|---|---|---|
| F1 | Table body (4 hard-coded rows) | LITERAL | P1 | `GET /admin/faculty?limit&offset` |
| F2 | "Add Faculty" button | DEAD (`disabled`) | P1 | `POST /admin/faculty` — needs a create form |
| F3 | Per-row "Manage" buttons (×4) | DEAD (`disabled`) | P1 | `GET/PATCH/DELETE /admin/faculty/{id}` |
| F4 | "Export Roster" button | DEAD (`disabled`) | P1 | Client-side CSV from fetched data (no endpoint) |
| F5 | Search / filter controls | none present | P2 | `GET /admin/faculty?search&department&is_active` is supported — add controls |
| F6 | Row columns | LITERAL | P1 | Map to `FacultyResponse` (`employee_code`, `full_name`, `department`, `designation`, `is_active`) |

### 3.4 `pages/admin/courses.html`

| # | Control | Current state | Verdict | Target / endpoint |
|---|---|---|---|---|
| C1 | Table body (4 hard-coded rows) | LITERAL | P1 | `GET /admin/courses?limit&offset` |
| C2 | "Add Course" button | DEAD (`disabled`) | P1 | `POST /admin/courses` — needs a create form |
| C3 | Per-row "Curriculum" buttons (×4) | DEAD (`disabled`) | P2 | `GET /admin/enrollments` filtered by course — render enrolled students per course |
| C4 | Search / filter controls | none present | P2 | `GET /admin/courses?search&department&is_active` is supported |
| C5 | Row columns | LITERAL | P1 | Map to `CourseResponse` (`course_code`, `course_title`, `credits`, `department`, `is_active`) |

### 3.5 `pages/admin/notices.html`

| # | Control | Current state | Verdict | Target / endpoint |
|---|---|---|---|---|
| N1 | Notice list (3 hard-coded entries) | LITERAL | P1 | `GET /admin/announcements?status&category&audience&limit&offset` |
| N2 | "New Notice" button | DEAD (`disabled`) | P1 | `POST /admin/announcements` — needs a create form |
| N3 | Per-row "Edit" buttons (×3) | DEAD (`disabled`) | P1 | `GET/PATCH /admin/announcements/{id}` |
| N4 | Per-row "Archive" buttons (×3) | DEAD (`disabled`) | P1 | `POST /admin/announcements/{id}/archive` — there is **no delete**; archive is the delete |
| N5 | Per-row "View Log" button | DEAD (`disabled`) | P2 | No read-audit model. Show the announcement's own status/timestamps, or remove. |
| N6 | Status tabs (Draft/Published/Archived) | STATIC spans | P1 | `GET /admin/announcements?status=` — the endpoint supports exactly these values |
| N7 | Publish action | absent | P1 | `POST /admin/announcements/{id}/publish` — per-row button for draft notices |

### 3.6 `pages/admin/reports.html`

| # | Control | Current state | Verdict | Target / endpoint |
|---|---|---|---|---|
| R1 | "Export Report" primary button | DEAD (`disabled`) | P1 | Client-side CSV/print of the rendered report (no endpoint) |
| R2 | Academic-year filter-bar (2026-27 / 2025-26) | STATIC spans, cosmetic click only (`script.js:196`) | P2 | **No academic-year model exists.** Drop it or add a year dimension to the backend. A cosmetic tab must not imply filtering. |
| R3 | Report metric blocks | LITERAL | P1 | `GET /admin/reports/summary`, `/reports/enrollments`, `/reports/attendance` |
| R4 | "Average CGPA" / publication metrics | LITERAL | P0 | **Remove or hide.** `average_cgpa` and `faculty_publication_count` are `None` by design. Surface `notes[]` instead of a number. |
| R5 | Charts / distribution visuals | STATIC | P1 | `reports/enrollments` → `courses[]` + `by_department`; `reports/attendance` → per-course rates |




### 3.7 Cross-cutting (all admin pages)

| # | Concern | Current state | Verdict | Target |
|---|---|---|---|---|
| X1 | Session expiry handling | Missing | P0 | A 401 from any API call must clear UI state and redirect to `../login.html?reason=session-expired`. Today only `/auth/me` on page load checks the session; an expiry mid-session yields raw failures. |
| X2 | CSRF token handling | Broken | P0 | `csrfToken = readCsrfCookie()` (line 353) assigns an **undeclared** variable → implicit global in sloppy mode. The token is also read only after login; every unsafe call must re-read it (it can rotate). |
| X3 | API base URL | Hard-coded relative paths | P1 | All fetches use `/api/v1/...`. This works when FastAPI serves the frontend (the documented default — `main.py:98` mounts `pages/`, `js/`, `css/`), but breaks under VS Code Live Server on :5500. Introduce one shared constant + `fetchJSON` helper so both workflows work. |
| X4 | Error surfacing | Missing | P0 | Every admin page needs a consistent error banner; today failures are invisible or `alert()`-based (logout) |
| X5 | Loading / empty states | Missing | P0 | Tables need skeleton/loading + "no results" empty state. `.empty-state` exists at `dashboard.css:1585`; a spinner/skeleton does not — add to `admin.css`. |
| X6 | Logout failure path | `alert()` only | P1 | On non-204 or network error the user is stranded on a dead page. Redirect to login regardless, with a message; the session is server-revoked anyway on success. |
| X7 | Accessibility of dynamic rows | N/A | P1 | Rendered rows must use the existing `.data-table`, `.cell-title`, `.cell-subtext`, `.badge` classes (`dashboard.css` 1177–1290) so dynamic tables match the static design |
| X8 | Confirmation on destructive actions | Missing | P1 | Deactivate/archive are irreversible in the UI sense — require a confirm step |
| X9 | Pagination | Missing | P0 | Endpoints return `total`, `limit`, `offset`; tables need paging controls (limit max 100) |
| X10 | `.filter-tab` handler in `script.js` | Cosmetic only | P1 | Drive tab state from real filters where a backend filter exists (notices status) |
| X11 | No frontend test infrastructure | Missing | P2 | No `package.json`, no JS test runner, no browser tests — see §5 |

---

## 4. Prioritized implementation plan

Each item states the change, the files touched, and how it is verified.
Order matters: P0.1 unblocks everything else.

### P0.1 — Repair the login flow (blocking everything)

**Files:** `js/auth.js`

1. **Fix the nested submit handler (defect A).** The `form.addEventListener("submit", …)`
   at line 332 sits *inside* the `loginForm.addEventListener("submit", …)` at line 284.
   The first submit registers the API listener; the second submit both re-registers
   it and triggers it → duplicate `fetch` calls that accumulate with every attempt.
   **Fix:** hoist the API listener to a single top-level registration and merge the
   demo validation into that one handler. The validation at lines 288–325 must run
   *before* the fetch, in the same handler.
2. **Declare `csrfToken` (defect C).** Line 353 assigns to an undeclared identifier.
   Use a module-scoped `let csrfToken = null;` plus a `getCsrfToken()` accessor that
   re-reads the cookie on demand.
3. **Remove the dead `DEMO_LOGIN_KEY` block (defect B).** Lines 59 (a stray HTML
   comment *inside* a JS file — a syntax hazard, not a comment), 106–140
   (`readDemoLogin`, `saveDemoLogin`, `clearDemoLogin`) reference an identifier that
   does not exist. These three functions have **no call sites** — the guard at
   line 400 was already migrated to `/auth/me`. Delete them outright.
4. **Delete the stray HTML comment at line 59.** `<!-- … -->` is not valid
   JavaScript; in a classic script it parses as comparison operators and throws.
### P0.2 — Shared frontend API layer

**Files:** new `js/api.js`, `css/admin.css` (additions), `js/auth.js`

Create one module that every admin page imports:

* `API_BASE` — default `""` (same-origin, FastAPI-served). Allow a
  `?api=` query override or a `data-api-base` attribute on `<body>` so the Live
  Server workflow (`http://localhost:5500`) can point at `http://localhost:8000`.
  CORS already permits `:5500` per `.env`.
* `apiFetch(path, { method, body })` — wraps `fetch` with
  `credentials: "include"`, attaches `X-CSRF-Token` for unsafe methods from the
  readable `campus360_csrf` cookie, parses JSON, and **maps 401 to a single
  session-expired redirect** (X1).
* `readCsrfCookie()` — move here from `auth.js` so it has one definition.
* `renderError` / `renderEmpty` / `setLoading` helpers so error, empty, and
  loading states look identical on every page (X4, X5).

**Verify:** `GET /admin/students` from a logged-in admin page returns rows; a
401 redirects to login; no CSRF header on GET, header present on POST.

### P0.3 — Dashboard from live statistics

**Files:** `pages/admin/dashboard.html`, new `js/admin-dashboard.js`

1. Add `data-stat="total_students|active_faculty|active_courses|active_sessions"`
   hooks to the four stat tiles; replace the literal values with placeholders
   (`—`) so nothing false renders before the fetch resolves.
2. Fetch `GET /admin/reports/summary` on load; fill each tile; format numbers
   with `toLocaleString()`.
3. Remove the invented "↑ 4.8% enrollment growth" (D5) unless a delta is added.
4. Populate the department KPI rows from `GET /admin/reports/enrollments`
   (`by_department`), joining faculty counts per department (D6).
5. Replace the fake activity trail with real recent items — e.g. the latest
   announcements from `GET /admin/announcements?limit=5` (D7).
6. Add loading skeletons and an error banner with a Retry button (X4, X5).

**Verify:** with an empty DB the tiles show `0` (not blanks); with rows they
match the DB counts; a forced 500 shows the error banner, not stale literals.

### P0.4 — Students CRUD

**Files:** `pages/admin/students.html`, new `js/admin-students.js`

1. Render the table from `GET /admin/students?limit&offset`; remove the 5 static
   rows; use `.data-table` / `.cell-title` / `.cell-subtext` / `.badge` classes (X7).
### P1 — Remaining admin surfaces

| Item | Work | Files |
|---|---|---|
| P1.1 | Faculty: list, create, edit, deactivate, search/filter, CSV export | `pages/admin/faculty.html`, `js/admin-faculty.js` |
| P1.2 | Courses: list, create, edit, deactivate, search/filter | `pages/admin/courses.html`, `js/admin-courses.js` |
| P1.3 | Course curriculum panel: enrolled students per course from `GET /admin/enrollments` | `js/admin-courses.js` |
| P1.4 | Notices: list by status tab, create, edit, publish, archive (no delete) | `pages/admin/notices.html`, `js/admin-notices.js` |
| P1.5 | Reports: bind summary + enrolment + attendance endpoints to the metric blocks and charts; hide CGPA/publication tiles and show `notes[]` instead | `pages/admin/reports.html`, `js/admin-reports.js` |
| P1.6 | Active sidebar state on every admin page | `pages/admin/*.html` |
| P1.7 | Logout: redirect to login on every outcome; surface a message via query param | `js/auth.js` |
| P1.8 | Session-expiry redirect with `?reason=session-expired` and a notice on the login page | `js/auth.js`, `pages/login.html` |
| P1.9 | Export CSV (students, faculty, reports) built client-side from fetched data | `js/admin-*.js` |

### P2 — Deferred, needs a decision or new tooling

* **P2.1** Backend integration tests for the admin endpoints the frontend
  depends on (see §5 for what already exists).
* **P2.2** Browser/E2E tests for login → dashboard load → students CRUD.
* **P2.3** Decide on the missing data models before building those UI:
  * **Grade scale / CGPA** — no model exists; `average_cgpa` is `None` by design.
    Add a grades/CGPA model, or remove the tile permanently.
  * **Audit log** — no model exists; the activity trail needs one, or it keeps
    being derived from announcements.
  * **Academic year** — no model exists; the reports year filter needs one.
  * **Semester filter on students** — no query param exists.
* **P2.4** Move the `data-table-search` client-side filter out of `script.js`
  once search is server-side, so there is one source of truth.

---

## 5. Testing strategy

### What exists today

* **Backend:** `pytest` against a dedicated `campus360_test` database — 238
  tests passing. Covers auth, sessions, CSRF, role authorization, admin CRUD,
  soft-deactivation, and the report endpoints.
* **Frontend:** **nothing.** No `package.json`, no JS test runner, no DOM
  test environment, no browser automation. There is no way today to assert that
  a click produced the right request.

### Recommended additions (P2, after P0/P1 land)

1. **Backend integration tests** (Python, fits the existing `pytest` setup) for
   the exact request/response contracts the frontend relies on: pagination
   metadata, `department`/`search` filtering, the 409 duplicate case, the
   deactivate-not-delete semantics, and `ReportSummary` `notes[]` when CGPA is
   unavailable. These lock the contract the JS is coded against.
2. **A JS unit-test layer** (Vitest + jsdom, or plain Node with a DOM shim) for
   the pure logic that is easiest to get wrong: CSRF-header attachment,
   the 401→redirect path, CSV generation, and number/percentage formatting.
## 6. Assumptions, limitations, and risks

* **Soft delete by design.** `DELETE` on students/faculty/courses flips
  `is_active`; academic history references those rows. The UI must not present
  this as "Delete" without a confirmation.
* **No CSRF token on GET.** Correct and intended — only unsafe methods need it.
* **CSRF cookie name.** `campus360_csrf` (readable) and `campus360_session`
  (HttpOnly), from `settings` in `backend/app/core/config.py`.
* **Frontend serving.** The plan assumes FastAPI serves the frontend
  (same-origin relative `/api/v1/...`). The Live Server variant needs the
  `API_BASE` override in P0.2 — and note that `SameSite` cookie behaviour and
  the CORS list must both permit it.
* **No grade scale.** Any CGPA figure on the dashboard would be fabricated.
  The plan hides the tile rather than inventing a value.
* **No audit trail model.** The activity feed is derived from existing data
  until a real audit log is added.
* **Uncommitted work preserved.** `js/auth.js`, `pages/login.html` (modified)
  and `js/auth.txt` (untracked) contain pre-existing changes; P0.1 edits
  `js/auth.js` on top of them and must not discard them.
* **No frontend test infrastructure** exists today — the single biggest
  verification gap, addressed in §5 / P2.
* **Baseline verified:** `238 passed`; `ruff` clean; no database mutations were
  made during this audit.

---

## 7. Quick reference — file map

| Path | Role |
|---|---|
| `js/auth.js` | Login, session guard, logout, role routing — **contains all three P0 defects** |
| `js/script.js` | Global behaviours: anchor scroll, client-side table search, cosmetic filter tabs |
| `js/api.js` | **New in P0.2** — API base, CSRF-aware fetch, error/empty/loading helpers |
| `js/admin-dashboard.js` | **New in P0.3** |
| `js/admin-students.js` | **New in P0.4** |
| `js/admin-{faculty,courses,notices,reports}.js` | **New in P1** |
| `css/admin.css` | Admin-specific styles; add spinner/skeleton/banner rules here |
| `css/dashboard.css` | Shared tokens + `.data-table`, `.badge`, `.empty-state` (1585) |
| `backend/app/api/admin.py` | All admin routes |
| `backend/app/api/auth.py` | Login / logout / me |
| `backend/app/api/deps.py` | `require_csrf`, `require_admin`, `require_roles` |
| `backend/app/schemas/admin.py` | `ReportSummary` (with `notes[]`), list-response shapes |
| `backend/app/main.py` | Router mounting + static mounts (line 98) |

---

## Appendix A — Admin endpoint inventory

Base prefix `/api/v1`. All routes require a valid session cookie; all unsafe
methods additionally require the `X-CSRF-Token` header matching the
`campus360_csrf` cookie.

### Students
| Method | Path | Notes |
|---|---|---|
| GET | `/admin/students` | `search`, `department`, `is_active`, `limit` (≤100), `offset` → `{items, total, limit, offset}` |
| GET | `/admin/students/{id}` | single record |
| POST | `/admin/students` | 409 on duplicate email/student code |
| PATCH | `/admin/students/{id}` | partial update |
| DELETE | `/admin/students/{id}` | **deactivates** (`is_active=false`) |

### Faculty
| Method | Path | Notes |
|---|---|---|
| GET | `/admin/faculty` | `search`, `department`, `is_active`, `limit`, `offset` |
| GET | `/admin/faculty/{id}` | single record |
| POST | `/admin/faculty` | create |
| PATCH | `/admin/faculty/{id}` | partial update |
| DELETE | `/admin/faculty/{id}` | **deactivates** |

### Courses
| Method | Path | Notes |
|---|---|---|
| GET | `/admin/courses` | `search`, `department`, `is_active`, `limit`, `offset` |
| GET | `/admin/courses/{id}` | single record |
| POST | `/admin/courses` | create |
| PATCH | `/admin/courses/{id}` | partial update |
| DELETE | `/admin/courses/{id}` | **deactivates** |

### Enrollments
| Method | Path | Notes |
|---|---|---|
| GET | `/admin/enrollments` | `student_id`, `course_id`, `status`, `limit`, `offset`. Returns a **bare array** `EnrollmentResponse[]` — no `{items,total}` envelope. |
| POST | `/admin/enrollments` | enrol a student into a course |
| PATCH | `/admin/enrollments/{id}/status` | `status` is a **query parameter** (`?status=...`), not a JSON body field. **No DELETE route** — an enrolment row cannot be removed via the admin API. |

### Announcements
| Method | Path | Notes |
|---|---|---|
| GET | `/admin/announcements` | `status` (draft/published/archived), `category`, `audience`, `limit`, `offset` |
| GET | `/admin/announcements/{id}` | single record |
| POST | `/admin/announcements` | create (default status `draft`) |
| PATCH | `/admin/announcements/{id}` | update content/metadata |
| POST | `/admin/announcements/{id}/publish` | draft → published |
| POST | `/admin/announcements/{id}/archive` | → archived (**no delete route**) |

### Reports
| Method | Path | Notes |
|---|---|---|
| GET | `/admin/reports/summary` | totals + `average_cgpa` / `faculty_publication_count` = `None` + `notes[]` |
| GET | `/admin/reports/enrollments` | `total_enrollments`, `courses[]` (per-course), `by_department` (dict) |
| GET | `/admin/reports/attendance` | `total_records`, `institution_attendance_rate`, `courses[]` (per-course rate) |

**Only three report routes exist.** There is **no** `/reports/performance` route —
`pass_percentage` is a field on `/reports/summary`, not a separate endpoint.

### Auth
| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | sets `campus360_session` + `campus360_csrf`; returns the authoritative role |
| POST | `/auth/logout` | revokes the session; requires CSRF |
| GET | `/auth/me` | session probe used by the page guard |


   This needs a `package.json` — the project currently has none.
3. **Browser E2E** (Playwright) for the three critical journeys:
   login → redirect to the right dashboard; dashboard tiles show DB counts;
   students create → edit → deactivate round-trips and survives a reload.
   Playwright is not currently installed (only `npx playwright` on demand).

**Honest limitation:** until at least (2) or (3) exists, the frontend work
cannot be automatically verified, and this audit's behavioural claims rest on
code reading plus manual browser checks.

---


2. Search → `?search=` with debounce (S2). Department select → `?department=`,
   options populated from the live `by_department` data (S4).
3. Pagination controls driven by `total` / `limit` / `offset` (X9).
4. "Add Student" → a form (inline panel or `<dialog>`) posting to
   `POST /admin/students`; on 409 show the duplicate-email/code message (S3).
5. Per-row "Manage" → edit form posting `PATCH /admin/students/{id}`; delete
   calls `DELETE /admin/students/{id}` which **deactivates** — label the button
   "Deactivate", not "Delete", and confirm first (S8, X8).
6. Toggle an `is_active` filter so deactivated students stay reachable (the
   endpoint supports `is_active`).

**Verify:** create → appears in list; edit → row updates; deactivate → row shows
an inactive badge and the record still exists in the DB (soft delete).


5. **Resolve the role mismatch (defect D).** The backend returns the authoritative
   role. Use it for routing (already done) and, if the chosen pill disagrees, show a
   clear message and route to the server's role — or drop the pill from the form.
6. **Handle non-email Campus IDs.** `LoginRequest.email` is `EmailStr`; a numeric
   Campus ID yields 422. Either the backend must accept an identifier, or the
   frontend must state that an email is required. Decide explicitly — do not leave
   the placeholder promising "Campus ID".

**Verify:** manual — one submit = one request (Network tab); failed login shows the
401 message; successful login lands on the role's dashboard.

