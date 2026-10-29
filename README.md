# Campus360

> A centralized educational management platform for a connected campus.

Campus360 is a web-based educational management system designed to bring
academic activities into one centralized platform.

It provides role-based interfaces for **Students**, **Faculty**, and
**Administrators**, covering attendance, timetables, assignments, marks and
results, announcements, courses, notices, and academic records.

---

## 🚀 Current Status (honest snapshot)

Development proceeds in stages. This is what is **actually working today**:

| Area | Status |
|---|---|
| Landing / Login / Student / Faculty / Admin pages | ✅ Complete (frontend) |
| Frontend navigation & responsive UI | ✅ Complete |
| `uv` + Python backend foundation | ✅ Complete (Stage 1) |
| FastAPI app, health endpoints, OpenAPI docs | ✅ Working |
| Configuration via `.env` (pydantic-settings) | ✅ Working |
| SQLAlchemy + Psycopg 3 + Alembic wiring | ✅ Working (verified to the DB socket) |
| Static frontend served by FastAPI (one origin) | ✅ Working |
| Lint (ruff) + tests (pytest) | ✅ Running |
| PostgreSQL database & application user | ✅ Complete (Stage 1) |
| Database schema / 11 tables / 9 enums | ✅ Complete (Stage 2) |
| Initial Alembic migration (applied) | ✅ Complete (Stage 2) |
| Real authentication & authorization | ✅ Server sessions, role checks, CSRF |
| Academic features (courses, attendance, marks, …) | ✅ Backend APIs implemented |
| Frontend ↔ API integration | 🚧 Dashboard, admin registries/reports, and core student/faculty reads wired |

### Current authentication

The login page sends credentials to `POST /api/v1/auth/login`. FastAPI creates
an HttpOnly server-side session and a readable CSRF cookie; protected pages
verify the session with `GET /api/v1/auth/me`. The frontend never stores a
password or session token and uses the authenticated server role for routing.
See [frontend verification](docs/FRONTEND-VERIFICATION.md) for manual checks
and the documented endpoint limitations.

---

## 🛠️ Technology Stack

**Frontend (existing, unchanged):** HTML5 · CSS3 · Vanilla JavaScript ·
`fetch()` + JSON (added as pages are wired to the API)

**Backend:** Python 3.14 · FastAPI · Uvicorn · Pydantic v2 · pydantic-settings

**Database:** PostgreSQL 18 · SQLAlchemy 2.x · Psycopg 3 · Alembic

**Tooling:** `uv` (environment, dependencies, lockfile) · pytest · httpx/httpx2
(TestClient) · Ruff

No Jinja2, no frontend framework, no JavaScript backend.

---

## 📦 Required Software

| Tool | Notes |
|---|---|
| [uv](https://docs.astral.sh/uv/) | Installs Python deps + manages `.venv`. Tested with `uv 0.12.21`. |
| Python ≥ 3.14 | uv can fetch one automatically (`uv python install 3.14`). |
| PostgreSQL 18 | Windows service `postgresql-x64-18`, port `5432`. |
| pgAdmin 4 | Optional; convenient for running SQL and browsing data. |

> `psql` is installed at `C:\Program Files\PostgreSQL\18\bin\psql.exe` but is
> **not on PATH** by default. In PowerShell use the full path, or use the
> pgAdmin Query Tool instead.

---

## 🗄️ PostgreSQL Setup (one-time, manual)

You need the **postgres superuser password** (set during PostgreSQL
installation). Do this in **pgAdmin 4 → Query Tool** (connected to the
`postgres` database), or in `psql` via the full path above:

```sql
-- 1) Dedicated application login. NEVER a superuser:
--    LOGIN only, no SUPERUSER/CREATEDB/CREATEROLE.
CREATE ROLE campus360_user WITH LOGIN PASSWORD 'choose-a-strong-password-here';

-- 2) The two databases the app uses, owned by that role so it can create
--    and alter its own tables (but nothing outside them).
CREATE DATABASE campus360_db   OWNER campus360_user;
CREATE DATABASE campus360_test OWNER campus360_user;  -- used ONLY by pytest
```

Replace the placeholder with **your own strong password** (16+ characters).
Because the backend URL-encodes credentials automatically, passwords may
safely contain characters like `@ : / ! #`.

**Rules:**

- Never reuse the `postgres` superuser password for the application.
- Never write the real password into any committed file — only into the
  git-ignored `.env`.
- Never drop or recreate databases that contain data you care about.

Verify afterwards (Query Tool / psql):

```sql
SELECT rolsuper FROM pg_roles WHERE rolname = 'campus360_user';  -- expected: f
-- psql only: \l   should list campus360_db and campus360_test
```

---

## ⚙️ Environment Configuration (`.env`)

```powershell
# from the project root, in PowerShell:
Copy-Item .env.example .env
```

Then open `.env` in your editor and set **at minimum**:

| Variable | What to put |
|---|---|
| `CAMPUS360_DB_PASSWORD` | The password you chose for `campus360_user` |
| `CAMPUS360_SECRET_KEY` | Generate: `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"` |

All other values have working development defaults (see `.env.example`).
`.env` is git-ignored — **never commit it**. Only `backend/app/core/config.py`
reads it; no other module touches environment variables.

---

## 🧰 Setup Commands (Windows PowerShell, from the project root)

```powershell
# 1) Create .venv and install ALL dependencies (runtime + dev) from uv.lock
uv sync

# 2) Start API + frontend (after creating the database & .env, see above)
uv run uvicorn app.main:app --app-dir backend --port 8000

# 3) In a second terminal — verify real connectivity:
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/health/db    # must return {"status":"ok",...}

# 4) Database migrations (schema lives in alembic/versions/)
uv run alembic upgrade head
```

---

## ▶️ Running the Application

### Recommended: one origin (simplest, no CORS issues)

```powershell
uv run uvicorn app.main:app --app-dir backend --port 8000 --reload
```

Open **http://localhost:8000** — FastAPI serves the *existing* frontend
(index, css, js, assets, pages) and the API from the same origin, so session
cookies will work without CORS complications once authentication lands.

> `--app-dir backend` puts the `app/` package on `sys.path` (the project is
> not pip-installed; this keeps the layout simple).

### Alternative: frontend served separately (e.g. VS Code Live Server)

Run Live Server on the project root (typically http://localhost:5500) and the
API on :8000; pages must then call `http://localhost:8000/api/...`.
CORS already allows `localhost:5500` via `CAMPUS360_CORS_ORIGINS` in `.env`.

### API documentation

| URL | What |
|---|---|
| http://localhost:8000/docs | Swagger UI (interactive) |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/openapi.json | Raw OpenAPI schema |

---

## 🧪 Tests & Linting

```powershell
# pytest — runs against the dedicated campus360_test database.
# backend/tests/conftest.py forces CAMPUS360_DB_NAME=campus360_test, so tests
# can never touch your development data.
uv run pytest

# ruff — lint + format check (use `--fix` / `format` to apply changes)
uv run ruff check backend alembic
uv run ruff format --check backend alembic

# JavaScript syntax check (no build step; plain browser scripts)
Get-ChildItem js\*.js, tests\browser\*.js | ForEach-Object { node --check $_.FullName }
```

### Browser tests (Playwright)

The browser suite in `tests/browser/` drives the real pages against a running
server. `workflows.spec.js` **creates records** (students, faculty, courses,
notices), so point it at a server whose `CAMPUS360_DB_NAME` is a dedicated,
disposable database — never at data you care about.

```powershell
npm install                     # installs @playwright/test from package.json
npx playwright install chromium # one-time browser download

# Terminal 1: serve the app on the port the suite expects, on a test database
$env:CAMPUS360_DB_NAME = "campus360_e2e"   # created like campus360_test
uv run alembic upgrade head
uv run python backend/scripts/create_user.py --email admin@example.edu --role admin
uv run uvicorn app.main:app --app-dir backend --port 8099

# Terminal 2: credentials of that admin account (never committed)
$env:CAMPUS360_E2E_ADMIN_EMAIL = "admin@example.edu"
$env:CAMPUS360_E2E_ADMIN_PASSWORD = "<the password you chose>"
npx playwright test
```

`CAMPUS360_E2E_BASE_URL` overrides the default `http://127.0.0.1:8099`.

---

## 🏗️ Architecture & Folder Structure

```text
Browser (existing HTML/CSS/JS, fetch() + JSON)
        │  HTTP — same origin by default
        ▼
FastAPI  (backend/app: routers under /api/v1, CORS, static frontend)
        │  Pydantic request/response schemas
        ▼
SQLAlchemy 2 sessions  (backend/app/db/session.py — one Session per request)
        │  Psycopg 3 driver
        ▼
PostgreSQL 18  (campus360_db; schema managed by Alembic — never create_all)

Alembic (alembic/) — versioned migrations in alembic/versions/
```

```text
Campus360/
├── index.html                 # landing page (served by FastAPI at /)
├── css/                       # style.css, auth.css, dashboard.css, student/faculty/admin
├── assets/                    # logo & imagery
├── js/
│   ├── script.js              # shared shell: skip link, nav state, corner toasts
│   ├── api.js                 # same-origin API client, CSRF token, expiry redirect
│   ├── portal.js              # page data binding, filters, dialogs, CSV import/export
│   ├── auth.js                # route guard, sign-in, role check, sign-out
│   └── activate.js            # public account-activation form
├── pages/
│   ├── login.html
│   ├── activate.html          # target of the emailed activation link
│   └── student/  faculty/  admin/      # role dashboards & pages
├── backend/
│   ├── app/
│   │   ├── main.py            # app factory: CORS, routers, static mounts, /
│   │   ├── core/
│   │   │   ├── config.py      # Settings: .env → typed config; builds the DB URL
│   │   │   ├── security.py    # Argon2id hashing, session/CSRF token helpers
│   │   │   ├── rate_limit.py  # failed-attempt throttle for login/activation
│   │   │   ├── email.py       # activation email providers (log / smtp / none)
│   │   │   └── programmes.py, departments.py  # catalogue policy lists
│   │   ├── db/
│   │   │   ├── base.py        # DeclarativeBase shared by all models
│   │   │   └── session.py     # engine, sessionmaker, get_db dependency
│   │   ├── api/
│   │   │   ├── deps.py        # session, CSRF and role guards shared by routers
│   │   │   ├── auth.py        # login, logout, /auth/me, account activation
│   │   │   ├── student.py     # student-scoped read endpoints
│   │   │   ├── faculty.py     # faculty-scoped assignment/mark endpoints
│   │   │   ├── admin.py       # admin CRUD, enrollments, reports, notices
│   │   │   └── health.py      # GET /api/v1/health, GET /api/v1/health/db
│   │   ├── models/            # SQLAlchemy models (see below)
│   │   ├── schemas/           # request/response models
│   │   └── services/          # business rules: admin, faculty, student, auth,
│   │                          # activation, bulk_import / faculty_bulk_import
│   │                          # (+ import_common, the shared CSV helpers)
│   ├── scripts/create_user.py # local CLI to provision accounts (first admin)
│   └── tests/                 # pytest: auth, authorization, admin, faculty, student, models
├── tests/browser/             # Playwright end-to-end suite (see "Browser tests")
├── alembic/                   # migration env + versions/ (schema history)
├── alembic.ini                # NO credentials in here (URL comes from .env)
├── pyproject.toml             # dependencies + ruff + pytest configuration
├── uv.lock                    # locked dependency versions (commit this)
├── .env.example               # template with placeholders (commit this)
├── .env                       # YOUR secrets (git-ignored — never commit)
└── README.md
```

**Design rules in force:**

- One module per concern; no giant `main.py`.
- Routes call services/repositories (from Stage 4 onward) — not raw ORM in handlers.
- API responses use Pydantic schemas, never raw ORM models.
- Schema changes only via Alembic migrations — `Base.metadata.create_all()`
  is not the schema management strategy.

---

## 🔐 Security Notes (current + planned)

Already in place:

- Secrets only in git-ignored `.env`; `SecretStr` redacts them from logs/reprs.
- SQLAlchemy `URL` masks the password whenever rendered as text.
- Static mounts expose only `css/js/assets/pages` — `.env`, `backend/`, and
  `.git/` are not reachable over HTTP (verified with 404 checks).
- Database errors return a generic 503; details stay in server logs.
- The application connects as a **non-superuser** role.
- Argon2id password hashing; opaque server-side sessions (HttpOnly cookie)
  with a synchronizer CSRF token on every state-changing request.
- Every API route enforces its role server-side; faculty may only touch
  courses they lead, and marks/attendance only for students enrolled in them.
- Failed sign-ins are throttled per client + email (5 per 15 minutes) and
  activation attempts per client. The counters live in the server process:
  they reset on restart and are not shared between several workers, so a
  multi-worker deployment also needs a proxy-level rate limit.
- CSV imports enforce the size/row limits server-side and re-validate every
  row on confirm; exported CSV cells are protected against formula injection.

Still open: request-body size limits belong in the reverse proxy (the import
limit is checked after the body is read), and the `log` email provider never
records activation links, so imported accounts can only be activated once a
real provider (`CAMPUS360_EMAIL_PROVIDER=smtp`) is configured.

This project is **not** claimed production-secure until deployment, HTTPS,
hardening, and security tests have been reviewed.

---

## 🔍 Troubleshooting

| Symptom | Likely cause & fix |
|---|---|
| `password authentication failed for user "campus360_user"` | Role missing, or password in `.env` doesn't match → re-check `CREATE ROLE` and `.env`. |
| `database "campus360_db" does not exist` | Run the `CREATE DATABASE` statements (correct target DB!). |
| `pydantic ValidationError ... db_password ... secret_key` | `.env` missing/incomplete → `Copy-Item .env.example .env`, fill both values. |
| `psql : term not recognized` | Not on PATH → `& "C:\Program Files\PostgreSQL\18\bin\psql.exe" ...` or pgAdmin. |
| Connection refused on port 5432 | Service `postgresql-x64-18` stopped → Windows Services or `Start-Service postgresql-x64-18`. |
| Port 8000 already in use | Start with `--port 8001` (update CORS/origin if the frontend is split). |
| CORS error in browser | Frontend on another origin → add it to `CAMPUS360_CORS_ORIGINS` (JSON array) and restart. |
| `alembic` can't import `app` | Run it from the project root (`env.py` fixes `sys.path` itself). |
| Tests: 503 on `/api/v1/health/db` | `campus360_test` missing or password mismatch — create it; keep `.env` in sync. |

---

## 🗓️ Development Roadmap

1. **Stage 1 — Audit & foundation** ✅
2. **Stage 2 — Database design** ✅ 11 tables, constraints, indexes, initial migration
3. **Stage 3 — Authentication** ✅ Argon2id password hashing, server sessions, CSRF, role authorization, tests
4. **Stage 4 — Core academic features** ✅ courses, enrollments, timetable, attendance,
   assignments, marks, announcements
5. **Stage 5 — Frontend integration** ✅ each page binds to the authenticated API; screens the
   API cannot serve say so instead of showing sample data
6. **Stage 6 — Testing & quality** ✅ backend suite, Ruff, JS syntax checks
7. **Stage 7 — Documentation & final audit** ✅ README and docs/FRONTEND-VERIFICATION.md

---

## ✨ Features (frontend, per portal)

- **Public:** landing page, hero, features, role overview, contact — responsive.
- **Login:** sign-in by institutional email, role selection, inline validation,
  role-based routing, expired-session and wrong-role messages.
- **Account activation:** imported students/faculty set their own password from
  the emailed link (`/pages/activate.html?token=...`).
- **Student:** dashboard, attendance, timetable, assignments, marks & results,
  announcements, academic records.
- **Faculty:** dashboard, attendance, assignments, marks, announcements.
- **Admin:** dashboard, student & faculty management (including CSV import with
  review step and CSV export), courses, enrollments, notices, reports.

---

© 2026 Campus360. Centralized Educational Management Platform.
