/* Page-level API bindings. This file deliberately uses the existing markup
   and replaces demo rows only after a successful, authorized API response. */
(function () {
  const api = window.Campus360Api;
  if (!api) return;

  const page = location.pathname.split("/").pop();
  const role = location.pathname.split("/").slice(-2, -1)[0];

  function ready(callback) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", callback);
    else callback();
  }

  function text(value, fallback = "—") {
    return value === null || value === undefined || value === "" ? fallback : String(value);
  }

  function escape(value) {
    const node = document.createElement("span");
    node.textContent = text(value, "");
    return node.innerHTML;
  }

  /* Every row of a list endpoint, 100 per request (the API maximum). Pickers
     and filters must not silently stop at the first page. Paginated
     endpoints return {items, total}; the enrollment list is a plain array. */
  async function fetchAll(path) {
    const items = [];
    const separator = path.includes("?") ? "&" : "?";
    for (let page = 0, offset = 0; page < 500; page += 1) {
      const batch = await api.get(`${path}${separator}limit=100&offset=${offset}`);
      const rows = Array.isArray(batch) ? batch : batch.items;
      items.push(...rows);
      offset += rows.length;
      if (rows.length < 100 || (!Array.isArray(batch) && offset >= batch.total)) break;
    }
    return items;
  }

  function setTableMessage(table, message) {
    const body = table?.querySelector("tbody");
    if (!body) return;
    const columns = table.querySelectorAll("thead th").length || 1;
    body.innerHTML = `<tr><td colspan="${columns}" class="table-state">${escape(message)}</td></tr>`;
  }

  function setPageMessage(message, kind = "info") {
    let node = document.getElementById("api-page-message");
    if (!node) {
      node = document.createElement("p");
      node.id = "api-page-message";
      node.className = "api-message";
      document.querySelector(".content-wrapper")?.prepend(node);
    }
    node.textContent = message;
    node.hidden = !message;
    node.dataset.kind = kind;
  }

  /* Result of an ACTION (created / updated / revoked). A corner toast survives
     the table re-render that follows a save, which the inline page message
     does not: that one is reserved for the page's data state. Falls back to
     the inline message if the shared shell script is unavailable. */
  function notify(message, kind = "success") {
    if (typeof window.Campus360Notify === "function") {
      window.Campus360Notify(message, kind);
    } else {
      setPageMessage(message, kind === "error" ? "error" : "info");
    }
  }

  /* A few faculty workflows reload the page after saving (the table is built
     from several endpoints). The result is handed across the reload in
     sessionStorage so the new page still confirms what happened. */
  const flashKey = "campus360:flash";

  function flash(message, kind = "success") {
    try {
      window.sessionStorage.setItem(flashKey, JSON.stringify({ message, kind }));
    } catch (error) {
      /* Private-mode storage failures are not worth interrupting the save. */
    }
  }

  function consumeFlash() {
    try {
      const raw = window.sessionStorage.getItem(flashKey);
      if (!raw) return;
      window.sessionStorage.removeItem(flashKey);
      const stored = JSON.parse(raw);
      if (stored && stored.message) notify(stored.message, stored.kind);
    } catch (error) {
      /* Ignore unreadable values; the save itself already succeeded. */
    }
  }

  function formatDate(value) {
    return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value)) : "—";
  }

  /* The student API returns wall-clock times as "HH:MM:SS" strings (the
     backend stores a time-of-day with no date part). Split them into the
     12-hour label the shared schedule badge expects. */
  function formatTimeParts(value) {
    const match = /^(\d{1,2}):(\d{2})/.exec(String(value || "").trim());
    if (!match) return { time: "—", meridiem: "" };
    const hours = Number(match[1]);
    const meridiem = hours >= 12 ? "PM" : "AM";
    const hour12 = hours % 12 === 0 ? 12 : hours % 12;
    return { time: `${String(hour12).padStart(2, "0")}:${match[2]}`, meridiem };
  }

  function formatTimeLabel(value) {
    const parts = formatTimeParts(value);
    return parts.meridiem ? `${parts.time} ${parts.meridiem}` : parts.time;
  }

  /* SubmissionStatus on the API is submitted | graded | returned; anything
     else means the student has no submission for that assignment. */
  function submissionBadge(status) {
    const known = {
      graded: ["badge-success", "Graded"],
      submitted: ["badge-primary", "Submitted"],
      returned: ["badge-warning", "Returned"],
    };
    const [className, label] = known[String(status || "").toLowerCase()] || ["badge-secondary", "Not submitted"];
    return `<span class="badge ${className}">${label}</span>`;
  }

  async function adminDashboard() {
    const stats = document.querySelector(".stats-grid");
    if (stats) stats.setAttribute("aria-busy", "true");
    try {
      const [summary, enrollments] = await Promise.all([
        api.get("/admin/reports/summary"),
        api.get("/admin/reports/enrollments"),
      ]);
      /* ReportSummary field-for-field: active_students, active_faculty,
         active_courses and active_sessions. The labels and the supporting
         text are set together with the value so a tile can never show a
         real number under a label that describes something else. */
      renderStatCards([
        { label: "Active students", value: summary.active_students, meta: `${summary.total_students} student profiles in total` },
        { label: "Active faculty", value: summary.active_faculty, meta: `${summary.total_faculty} faculty profiles in total` },
        { label: "Active courses", value: summary.active_courses, meta: `${summary.total_courses} in the course catalogue` },
        { label: "Open sessions", value: summary.active_sessions, meta: "Server-side login sessions" },
      ]);
      const banner = document.querySelector(".welcome-banner p");
      if (banner) {
        banner.textContent = `${summary.active_students} active students • ${summary.total_enrollments} active enrollments • ${summary.active_courses} active courses.`;
      }
      const rows = document.querySelector(".grid-2-1 .card .card-body");
      if (rows) {
        const departments = Object.entries(enrollments.by_department);
        rows.innerHTML = departments.length
          ? departments.map(([department, count]) => `<div class="kpi-row"><span class="kpi-title">${escape(department)}</span><span class="kpi-value">${count} active enrollments</span></div>`).join("")
          : '<p class="empty-state">No active enrollment data is available.</p>';
      }
      renderAuditUnavailable();
    } catch (error) {
      setPageMessage(`Unable to load dashboard data: ${error.message}`, "error");
      clearStatCards();
      const rows = document.querySelector(".grid-2-1 .card .card-body");
      if (rows) rows.innerHTML = '<p class="empty-state">Department enrollment data is unavailable right now.</p>';
      renderAuditUnavailable();
    } finally {
      if (stats) stats.removeAttribute("aria-busy");
    }
  }

  /* The admin API exposes no audit-log endpoint, so this panel must say so
     rather than keep showing a sample trail. */
  function renderAuditUnavailable() {
    const log = document.querySelector(".activity-list");
    if (log) log.innerHTML = '<div class="activity-item"><div class="activity-details"><span class="activity-title">System audit data is unavailable</span><span class="activity-meta">The current API does not expose an audit-log endpoint.</span></div></div>';
  }

  /* Column headers are derived from the SAME response fields the rows use, so
     a column can never advertise something the API does not return. */
  const adminTableHeaders = {
    students: ["Roll Number", "Name & contact", "Degree program", "Semester", "Attendance", "Status", "Actions"],
    faculty: ["Faculty code", "Name, designation & contact", "Department", "Courses led", "Status", "Actions"],
    courses: ["Course code", "Title & description", "Department", "Credits", "Lead faculty", "Status", "Actions"],
    announcements: ["Title", "Audience", "Category", "Published", "Status", "Actions"],
  };

  const adminListTitles = {
    students: (total) => `Student Roster (${total} total)`,
    faculty: (total) => `Institutional Faculty Members (${total} total)`,
    courses: (total) => `Curriculum Offerings (${total} total)`,
    announcements: (total) => `Campus-Wide Announcement Registry (${total} total)`,
  };

  /* Resources whose list endpoint accepts a server-side `search` filter. */
  const adminSearchable = ["students", "faculty", "courses"];

  const adminExportHeaders = {
    students: ["Roll Number", "Full name", "Email", "Degree program", "Semester", "Attendance %", "Status"],
    faculty: ["Faculty code", "Full name", "Email", "Designation", "Department", "Courses led", "Status"],
    courses: ["Course code", "Title", "Description", "Department", "Credits", "Lead faculty", "Status"],
    announcements: ["Title", "Body", "Audience", "Category", "Published at", "Status"],
  };

  const adminExportRows = {
    students: (item) => [item.student_code, item.full_name, item.email, item.degree_program, item.current_semester, item.attendance_percentage, item.is_active ? "Active" : "Inactive"],
    faculty: (item) => [item.faculty_code, item.full_name, item.email, item.designation, item.department, item.assigned_courses, item.is_active ? "Active" : "Inactive"],
    courses: (item) => [item.code, item.title, item.description, item.department, item.credits, item.lead_faculty_name, item.is_active ? "Active" : "Inactive"],
    announcements: (item) => [item.title, item.body, item.audience, item.category, item.published_at, item.status],
  };

  /* Tiles for the list pages, read from the same summary endpoint the admin
     dashboard uses - every number is computed by the server. */
  const adminListStats = {
    students: (summary) => [
      { label: "Students", value: summary.total_students, meta: "Student profiles in total" },
      { label: "Active students", value: summary.active_students, meta: "Accounts with active status" },
      { label: "Inactive students", value: summary.total_students - summary.active_students, meta: "Deactivated but preserved" },
    ],
    faculty: (summary) => [
      { label: "Faculty members", value: summary.total_faculty, meta: "Faculty profiles in total" },
      { label: "Active faculty", value: summary.active_faculty, meta: "Accounts with active status" },
      { label: "Inactive faculty", value: summary.total_faculty - summary.active_faculty, meta: "Deactivated but preserved" },
    ],
    courses: (summary) => [
      { label: "Courses", value: summary.total_courses, meta: "Entries in the catalogue" },
      { label: "Active courses", value: summary.active_courses, meta: "Open for enrollment" },
      { label: "Inactive courses", value: summary.total_courses - summary.active_courses, meta: "Deactivated but preserved" },
    ],
  };

  function renderAdminRows(resource, items) {
    const table = document.querySelector(".data-table");
    const body = table?.querySelector("tbody");
    if (!body) return;
    replaceTableHeaders(table, adminTableHeaders[resource] || []);
    if (!items.length) return setTableMessage(table, `No ${labelPlural(resource)} found.`);
    const renderers = {
      students: (item) => `<tr><td class="student-roll"><span class="student-roll-code">${escape(item.student_code)}</span></td><td><span class="cell-title">${escape(item.full_name)}</span><span class="cell-subtext">${escape(item.email)}</span></td><td>${escape(item.degree_program)}</td><td>Semester ${text(item.current_semester)}</td><td>${item.attendance_percentage === null ? "—" : `${item.attendance_percentage}%`}</td><td><span class="badge ${item.is_active ? "badge-success" : "badge-warning"}">${item.is_active ? "Active" : "Inactive"}</span></td><td>${!item.is_active ? `<button class="btn btn-text btn-sm" data-record-id="${item.id}" data-action="activate">Activate</button>` : ""} <button class="btn btn-text btn-sm" data-record-id="${item.id}" data-action="details">View Details</button> <button class="btn btn-outline btn-sm" data-record-id="${item.id}" data-action="edit">Manage</button> ${item.is_active ? `<button class="btn btn-text btn-sm" data-record-id="${item.id}" data-action="deactivate">Deactivate</button>` : ""}</td></tr>`,
      faculty: (item) => `<tr><td><strong>${escape(item.faculty_code)}</strong></td><td><span class="cell-title">${escape(item.full_name)}</span><span class="cell-subtext">${escape(item.designation)} • ${escape(item.email)}</span></td><td>${escape(item.department)}</td><td>${item.assigned_courses}</td><td><span class="badge ${item.is_active ? "badge-success" : "badge-warning"}">${item.is_active ? "Active" : "Inactive"}</span></td><td>${!item.is_active ? `<button class="btn btn-text btn-sm" data-record-id="${item.id}" data-action="activate">Activate</button>` : ""} <button class="btn btn-outline btn-sm" data-record-id="${item.id}" data-action="edit">Manage</button> ${item.is_active ? `<button class="btn btn-text btn-sm" data-record-id="${item.id}" data-action="deactivate">Deactivate</button>` : ""}</td></tr>`,
      courses: (item) => `<tr><td><strong>${escape(item.code)}</strong></td><td><span class="cell-title">${escape(item.title)}</span><span class="cell-subtext">${escape(item.description)}</span></td><td>${escape(item.department)}</td><td>${item.credits} Credits</td><td>${escape(item.lead_faculty_name)}</td><td><span class="badge ${item.is_active ? "badge-success" : "badge-warning"}">${item.is_active ? "Active" : "Inactive"}</span></td><td><button class="btn btn-outline btn-sm" data-record-id="${item.id}" data-action="edit">Manage</button> ${item.is_active ? `<button class="btn btn-text btn-sm" data-record-id="${item.id}" data-action="deactivate">Deactivate</button>` : ""}</td></tr>`,
      announcements: (item) => `<tr><td><span class="cell-title">${escape(item.title)}</span><span class="cell-subtext">${escape(item.body)}</span></td><td>${escape(item.audience)}</td><td>${escape(item.category)}</td><td>${formatDate(item.published_at)}</td><td><span class="badge ${item.status === "active" ? "badge-success" : "badge-warning"}">${escape(item.status)}</span></td><td><button class="btn btn-outline btn-sm" data-record-id="${item.id}" data-action="edit">Edit</button> <button class="btn btn-text btn-sm" data-record-id="${item.id}" data-action="archive">${item.status === "active" ? "Archive" : "Publish"}</button></td></tr>`,
    };
    body.innerHTML = items.map(renderers[resource]).join("");
  }

  /* Keep these bounds in step with backend SEMESTER_POLICY in
     core/programmes.py. Both the create and edit dialogs render this same
     list, so they can never offer different semesters. */
  const SEMESTER_MIN = 1;
  const SEMESTER_MAX = 14;
  const SEMESTER_OPTIONS = Array.from({ length: SEMESTER_MAX - SEMESTER_MIN + 1 }, (_, index) => SEMESTER_MIN + index);

  function renderSemesterSelect(name, value) {
    /* Only a record that currently has no semester gets a blank choice. A
       PATCH omits blank fields, so offering "Not set" for a set semester would
       imply it can clear the value when it cannot. */
    const blank = value === "" ? '<option value="" selected>Not set</option>' : "";
    const options = SEMESTER_OPTIONS.map(
      (semester) => `<option value="${semester}" ${String(value) === String(semester) ? "selected" : ""}>${semester}</option>`,
    ).join("");
    return `<select id="form-${name}" name="${name}">${blank}${options}</select>`;
  }

  const adminForms = {
    students: [
      ["email", "Institutional email", "email", true], ["password", "Temporary password", "password", true],
      ["student_code", "Roll Number", "text", true], ["full_name", "Full name", "text", true],
      ["degree_program", "Degree programme", "programme-select"], ["current_semester", "Current semester", "semester-select"], ["phone", "Phone", "tel"],
      ["is_active", "Activate account", "bool"],
    ],
    faculty: [
      ["email", "Institutional email", "email", true], ["password", "Temporary password", "password", true],
      ["faculty_code", "Faculty code", "text", true], ["full_name", "Full name", "text", true],
      ["department", "Department", "department-select"], ["designation", "Designation", "designation-select"],
      ["is_active", "Activate account", "bool"],
    ],
    courses: [
      ["code", "Course code", "text", true], ["title", "Course title", "text", true],
      ["description", "Description", "textarea"], ["credits", "Credits", "number", true],
      ["department", "Department", "department-select"], ["lead_faculty_id", "Lead faculty", "faculty-select"],
    ],
    announcements: [
      ["title", "Title", "text", true], ["body", "Message", "textarea", true],
      ["category", "Category", "select", true, ["course_notice", "administrative", "examination", "event", "it_services", "research"]],
      ["audience", "Audience", "select", true, ["all", "students", "faculty", "admin"]],
    ],
  };

  /* Human labels for the resources the admin tables share. Used in action
     confirmations ("Student updated.") instead of the raw endpoint name. */
  const resourceLabels = {
    students: "Student",
    faculty: "Faculty member",
    courses: "Course",
    announcements: "Announcement",
  };

  const resourcePlurals = {
    students: "students",
    faculty: "faculty members",
    courses: "courses",
    announcements: "announcements",
  };

  function labelFor(resource) {
    return resourceLabels[resource] || "Record";
  }

  function labelPlural(resource) {
    return resourcePlurals[resource] || "records";
  }

  /* Validate the catalogue before it reaches the renderer. An error payload or
     a stale/partial backend must not be treated as an empty dropdown. */
  function isProgrammeCatalogue(payload) {
    return Array.isArray(payload)
      && payload.length > 0
      && payload.every((programme) => programme
        && typeof programme.code === "string" && programme.code
        && typeof programme.name === "string" && programme.name
        && typeof programme.group === "string" && programme.group);
  }

  /* Why the programme picker has no options. The specific cause matters: a
     stale backend (404), a role/session problem, a network outage and a
     malformed payload each need a different operator action. */
  function describeProgrammeCatalogueError(error) {
    if (error?.code === "MALFORMED_PROGRAMMES") {
      return "The programme catalogue response was malformed. Expected a non-empty list of programmes with code, name and group.";
    }
    if (error?.status === 401) return "Your session expired before the programme catalogue could load. Sign in again.";
    if (error?.status === 403) return "Your account is not authorized to load the programme catalogue (HTTP 403).";
    if (error?.status === 404) return "The programme catalogue endpoint was not found (HTTP 404). The server may be running an older build.";
    if (error?.status >= 500) return `The server failed to load the programme catalogue (HTTP ${error.status}).`;
    if (error?.status) return `The programme catalogue request failed (HTTP ${error.status}).`;
    if (error instanceof TypeError || /fetch|network/i.test(String(error?.message || ""))) {
      return "Could not reach the server to load the programme catalogue. Check that the backend is running and try again.";
    }
    return `The programme catalogue could not be loaded: ${error?.message || "unknown error"}.`;
  }

  /* A <select> of canonical programmes. The submitted value is the stable
     CODE (e.g. BTECH-CSE); the backend resolves it to the canonical display
     name. A legacy free-text value that no longer resolves is offered as a
     "keep" option, so editing an unrelated field can never silently rewrite
     or drop it. */
  function renderProgrammeSelect(name, current, programmes, required, errorMessage) {
    const id = `form-${name}`;
    if (!programmes.length) {
      const message = errorMessage || "The programme catalogue is unavailable.";
      return `<select id="${id}" name="${name}" disabled aria-describedby="${id}-error"><option>Programme selection unavailable</option></select><p id="${id}-error" class="field-error" role="alert">${escape(message)}</p>`;
    }
    const selectedCode = current?.programme_code || "";
    const legacyValue = current?.degree_program && !selectedCode ? current.degree_program : "";
    const groups = [];
    programmes.forEach((programme) => {
      if (!groups.includes(programme.group)) groups.push(programme.group);
    });
    const options = groups.map((group) => `<optgroup label="${escape(group)}">${programmes
      .filter((programme) => programme.group === group)
      .map((programme) => `<option value="${escape(programme.code)}" ${programme.code === selectedCode ? "selected" : ""}>${escape(programme.name)}</option>`)
      .join("")}</optgroup>`).join("");
    const legacy = legacyValue
      ? `<option value="${escape(legacyValue)}" selected>Uncatalogued: ${escape(legacyValue)} (keep)</option>`
      : "";
    return `<select id="${id}" name="${name}" ${required ? "required" : ""}><option value="">No programme selected</option>${legacy}${options}</select>`;
  }

  /* A <select> of canonical departments. The submitted value is the stable CODE
     (e.g. CSE); the backend resolves it to the canonical display name. A legacy
     free-text value that no longer resolves is offered as a "keep" option. */
  function renderDepartmentSelect(name, current, departments, required, errorMessage) {
    const id = `form-${name}`;
    if (!departments.length) {
      const message = errorMessage || "The department catalogue is unavailable.";
      return `<select id="${id}" name="${name}" disabled aria-describedby="${id}-error"><option>Department selection unavailable</option></select><p id="${id}-error" class="field-error" role="alert">${escape(message)}</p>`;
    }
    const selectedCode = current?.department_code || "";
    const legacyValue = current?.department && !selectedCode ? current.department : "";
    const options = departments
      .map((d) => `<option value="${escape(d.code)}" ${d.code === selectedCode ? "selected" : ""}>${escape(d.name)}</option>`)
      .join("");
    const legacy = legacyValue
      ? `<option value="${escape(legacyValue)}" selected>Uncatalogued: ${escape(legacyValue)} (keep)</option>`
      : "";
    return `<select id="${id}" name="${name}" ${required ? "required" : ""}><option value="">No department selected</option>${legacy}${options}</select>`;
  }

  /* Lead faculty picker grouped around the chosen department. When a
     department is selected, faculty whose stable department code matches that
     department appear first under a labelled optgroup; every other faculty
     member stays selectable under "Other faculty", so cross-department
     teaching is never blocked. Faculty without a catalogue department are
     treated as "Other faculty" and cannot break the picker. "No lead faculty"
     is always the first option and the submitted value stays a faculty id. */
  function renderFacultySelect(name, selectedId, faculty, departments, selectedDepartmentCode) {
    const id = `form-${name}`;
    const noFaculty = `<option value="">No lead faculty</option>`;
    if (!faculty.length) {
      return `<select id="${id}" name="${name}">${noFaculty}</select>`;
    }
    const target = String(selectedDepartmentCode || "").toUpperCase();
    const byName = (a, b) => a.full_name.localeCompare(b.full_name);
    const ordered = faculty.slice().sort(byName);
    const byDepartment = target
      ? ordered.filter((f) => String(f.department_code || "").toUpperCase() === target)
      : [];
    const other = target ? ordered.filter((f) => String(f.department_code || "").toUpperCase() !== target) : ordered;
    const option = (f) => `<option value="${escape(f.id)}" ${String(f.id) === String(selectedId) ? "selected" : ""}>${escape(f.full_name)} (${escape(f.faculty_code)})</option>`;
    let groups = "";
    if (target) {
      if (byDepartment.length) {
        const department = departments.find((d) => String(d.code).toUpperCase() === target);
        groups += `<optgroup label="Faculty in ${escape(department ? department.name : selectedDepartmentCode)}">${byDepartment.map(option).join("")}</optgroup>`;
      }
      groups += `<optgroup label="Other faculty">${other.map(option).join("")}</optgroup>`;
    } else {
      groups += ordered.map(option).join("");
    }
    return `<select id="${id}" name="${name}">${noFaculty}${groups}</select>`;
  }

  function renderDesignationSelect(name, current, designations, required) {
    const id = `form-${name}`;
    const value = current?.[name] ?? "";
    const supported = (designations || []).slice();
    const isLegacy = value && !supported.includes(value);
    const options = supported.map((d) => `<option value="${escape(d)}" ${d === value ? "selected" : ""}>${escape(d)}</option>`).join("");
    const legacy = isLegacy ? `<option value="${escape(value)}" selected>Uncatalogued: ${escape(value)} (keep)</option>` : "";
    return `<select id="${id}" name="${name}" ${required ? "required" : ""}><option value="">No designation</option>${legacy}${options}<option value="__custom__">Other (custom)…</option></select><input id="${id}-custom" name="${name}_custom" type="text" placeholder="Custom designation" hidden />`;
  }

  function showAdminForm(resource, current, onSave, context) {
    const editing = Boolean(current);
    const facultyOptions = (context && context.faculty) || [];
    const programmes = (context && context.programmes) || [];
    const programmeError = (context && context.programmeError) || "";
    const departments = (context && context.departments) || [];
    const departmentError = (context && context.departmentError) || "";
    const currentDepartment = current?.department;
    const selectedDepartmentCode = currentDepartment
      ? departments.find((d) => String(d.name).toLowerCase() === String(currentDepartment).toLowerCase())?.code || ""
      : "";
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog";
    /* Identity fields fixed once the record exists: the password and the
       faculty/course codes have no PATCH support. Students ARE different - the
       roll number and the login email both have PATCH support (the backend
       updates the linked users row for the email), so an admin can correct
       either without deleting and recreating the student. Faculty emails stay
       read-only until FacultyUpdate supports them. */
    const lockedFields = ["password", "faculty_code", "code"];
    if (resource !== "students") lockedFields.push("email");
    const fields = adminForms[resource].filter(([name]) => !editing || !lockedFields.includes(name));
    dialog.innerHTML = `<form method="dialog"><h2>${editing ? "Edit" : "Create"} ${resource.slice(0, -1)}</h2><p class="field-error" data-form-error hidden></p>${fields.map(([name, label, type, required, options]) => {
      const value = current?.[name] ?? "";
      const control = type === "programme-select"
        ? renderProgrammeSelect(name, current, programmes, required, programmeError)
        : type === "department-select"
          ? renderDepartmentSelect(name, current, departments, required, departmentError)
        : type === "designation-select"
          ? renderDesignationSelect(name, current, (context && context.designations) || [], required)
        : type === "semester-select"
          ? renderSemesterSelect(name, value)
        : type === "textarea"
          ? `<textarea id="form-${name}" name="${name}" ${required ? "required" : ""}>${escape(value)}</textarea>`
        : type === "select"
          ? `<select id="form-${name}" name="${name}" ${required ? "required" : ""}>${options.map((option) => `<option value="${option}" ${value === option ? "selected" : ""}>${option}</option>`).join("")}</select>`
          : type === "faculty-select"
            ? facultyOptions.length
              ? renderFacultySelect(name, value, facultyOptions, departments, selectedDepartmentCode)
              : `<input id="form-${name}" name="${name}" type="number" value="${escape(value)}" />`
          : type === "bool"
            ? `<input id="form-${name}" name="${name}" type="checkbox" value="on" ${value ? "checked" : ""} />`
            : `<input id="form-${name}" name="${name}" type="${type}" value="${escape(value)}" ${required ? "required" : ""}${type === "number" ? ' min="1" step="1"' : ""} />`;
      return `<div class="form-group${type === "textarea" ? " wide" : ""}"><label for="form-${name}">${label}</label>${control}<p class="field-error" id="form-${name}-field-error" data-field-error="${name}" role="alert" hidden></p></div>`;
    }).join("")}<div class="row-tight-lg"><button class="btn btn-outline" type="button" data-cancel>Cancel</button><button class="btn btn-primary" type="submit">${editing ? "Save changes" : "Create"}</button></div></form>`;
    document.body.append(dialog);
    /* Close AND detach. The close event handles Escape, but a dialog closed
       by the code below should not linger in the DOM where a later lookup
       could still find a stale copy. */
    const closeDialog = () => {
      dialog.close();
      dialog.remove();
    };
    dialog.querySelector("[data-cancel]").addEventListener("click", closeDialog);
    dialog.addEventListener("close", () => dialog.remove());
    const form = dialog.querySelector("form");
    const error = form.querySelector("[data-form-error]");
    const submitButton = form.querySelector("button[type=submit]");

    /* When the department changes, regroup the lead faculty picker so faculty
       in the chosen department appear first. All faculty stay selectable and
       the current choice is preserved where possible. */
    const departmentSelect = form.querySelector('select[name="department"]');
    if (departmentSelect) {
      const refreshFacultySelect = () => {
        const facultyNode = form.querySelector('select[name="lead_faculty_id"]');
        if (!facultyNode) return;
        facultyNode.outerHTML = renderFacultySelect(
          "lead_faculty_id",
          facultyNode.value,
          facultyOptions,
          departments,
          departmentSelect.value,
        );
      };
      departmentSelect.addEventListener("change", refreshFacultySelect);
    }

    const designationSelect = form.querySelector('select[name="designation"]');
    const designationCustom = form.querySelector('input[name="designation_custom"]');
    if (designationSelect && designationCustom) {
      const syncDesignation = () => {
        const custom = designationSelect.value === "__custom__";
        designationCustom.hidden = !custom;
        designationCustom.required = custom;
        if (!custom) designationCustom.value = "";
      };
      designationSelect.addEventListener("change", syncDesignation);
      syncDesignation();
    }

    const setDescribedBy = (control, id, enabled) => {
      const ids = new Set((control.getAttribute("aria-describedby") || "")
        .split(/\s+/)
        .filter(Boolean));
      if (enabled) ids.add(id);
      else ids.delete(id);
      if (ids.size) control.setAttribute("aria-describedby", Array.from(ids).join(" "));
      else control.removeAttribute("aria-describedby");
    };

    const clearFieldError = (name) => {
      const message = form.querySelector(`[data-field-error="${name}"]`);
      if (!message) return;
      message.textContent = "";
      message.hidden = true;
      message.closest(".form-group")?.classList.remove("has-error");
      const control = form.querySelector(`[name="${name}"]`);
      if (control) {
        control.removeAttribute("aria-invalid");
        setDescribedBy(control, message.id, false);
      }
    };

    const showFieldError = (name, message) => {
      const node = form.querySelector(`[data-field-error="${name}"]`);
      const control = form.querySelector(`[name="${name}"]`);
      if (!node || !control || typeof message !== "string" || !message) return false;
      node.textContent = message;
      node.hidden = false;
      node.closest(".form-group")?.classList.add("has-error");
      control.setAttribute("aria-invalid", "true");
      setDescribedBy(control, node.id, true);
      return true;
    };

    const clearFieldErrors = () => {
      form.querySelectorAll("[data-field-error]").forEach((node) => {
        if (node.dataset.fieldError) clearFieldError(node.dataset.fieldError);
      });
    };

    /* Editing one field clears only that field's duplicate/validation message;
       the other field keeps its own error until it too is edited or resubmitted. */
    const clearErrorOnEdit = (event) => {
      const name = event.target?.name;
      if (name && form.querySelector(`[data-field-error="${name}"]`)) {
        clearFieldError(name);
      }
    };
    form.addEventListener("input", clearErrorOnEdit);
    form.addEventListener("change", clearErrorOnEdit);

    let submitting = false;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submitting) return;
      if (!form.checkValidity()) return form.reportValidity();
      clearFieldErrors();
      error.textContent = "";
      error.hidden = true;
      const values = Object.fromEntries(new FormData(form));
      if (values.designation === "__custom__") {
        values.designation = String(values.designation_custom || "").trim();
        if (!values.designation) delete values.designation;
      }
      delete values.designation_custom;
      if (values.designation === "") delete values.designation;
      ["current_semester", "credits", "lead_faculty_id"].forEach((key) => {
        if (values[key] === "") delete values[key];
        else if (values[key] !== undefined) values[key] = Number(values[key]);
      });
      if (values.is_active === "on") values.is_active = true;
      if (resource === "courses" && !editing) values.is_active = true;
      /* A double click or Enter key must never issue two create/update
         requests. The guard covers both while the first request is in flight. */
      submitting = true;
      submitButton.disabled = true;
      try {
        await onSave(values);
        closeDialog();
      } catch (reason) {
        const fieldErrors = reason?.fieldErrors || {};
        let firstInvalid = null;
        Object.entries(fieldErrors).forEach(([name, message]) => {
          if (showFieldError(name, message) && !firstInvalid) {
            firstInvalid = form.querySelector(`[name="${name}"]`);
          }
        });
        if (firstInvalid) {
          /* The per-field messages are the error state; do not also repeat the
             same text in a generic banner. */
          error.textContent = "";
          error.hidden = true;
          firstInvalid.focus();
        } else {
          error.textContent = reason?.message || "Unable to save changes.";
          error.hidden = false;
        }
        submitButton.disabled = false;
      } finally {
        submitting = false;
      }
    });
    dialog.showModal();
  }

  async function showEnrollmentDialog(onChanged) {
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog";
    dialog.innerHTML = '<form method="dialog"><h2>Manage enrollments</h2><p class="field-error" data-form-error hidden></p><div data-enrollment-content aria-live="polite">Loading students and courses…</div><div class="row-tight-lg"><button class="btn btn-outline" type="button" data-cancel>Close</button><button class="btn btn-primary" type="submit" disabled>Enroll student</button></div></form>';
    document.body.append(dialog);
    const closeDialog = () => {
      dialog.close();
      dialog.remove();
    };
    dialog.querySelector("[data-cancel]").addEventListener("click", closeDialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();

    const form = dialog.querySelector("form");
    const content = dialog.querySelector("[data-enrollment-content]");
    const submitButton = form.querySelector("button[type=submit]");
    const errorNode = form.querySelector("[data-form-error]");

    const showError = (message) => {
      errorNode.textContent = message;
      errorNode.hidden = false;
    };
    const clearError = () => {
      errorNode.textContent = "";
      errorNode.hidden = true;
    };

    let students = [];
    let courses = [];
    let enrollments = [];
    try {
      /* Active-only course list: inactive catalogue entries cannot accept new
         enrollments. The backend validates the same rule regardless. */
      [students, courses, enrollments] = await Promise.all([
        fetchAll("/admin/students?is_active=true"),
        fetchAll("/admin/courses?is_active=true"),
        fetchAll("/admin/enrollments"),
      ]);
    } catch (error) {
      content.textContent = `Unable to load enrollment data: ${error.message}`;
      content.setAttribute("role", "alert");
      return;
    }

    const enrollmentFor = (studentId, courseId) =>
      enrollments.find((row) => row.student_id === studentId && row.course_id === courseId);

    const enrollmentRowsHtml = () => (enrollments.length
      ? enrollments.map((item) => {
        const action = item.status === "active"
          ? `<button type="button" class="btn btn-text btn-sm" data-enrollment-id="${item.id}" data-next-status="dropped">Drop</button>`
          : item.status === "dropped"
            ? `<button type="button" class="btn btn-outline btn-sm" data-enrollment-id="${item.id}" data-next-status="active">Reactivate</button>`
            : "—";
        return `<tr><td><span class="cell-title">${escape(item.student_name)}</span><span class="cell-subtext">${escape(item.student_code)}</span></td><td><span class="cell-title">${escape(item.course_title)}</span><span class="cell-subtext">${escape(item.course_code)}</span></td><td><span class="badge ${item.status === "active" ? "badge-success" : "badge-warning"}">${escape(item.status)}</span></td><td>${action}</td></tr>`;
      }).join("")
      : '<tr><td colspan="4">No enrollment records yet.</td></tr>');

    const refreshEnrollmentTable = () => {
      const rows = content.querySelector("[data-enrollment-rows]");
      if (rows) rows.innerHTML = enrollmentRowsHtml();
    };

    const updateCourseOptions = () => {
      const studentSelect = content.querySelector("#enrollment-student");
      const courseSelect = content.querySelector("#enrollment-course");
      const note = content.querySelector("[data-course-note]");
      if (!studentSelect || !courseSelect) return;
      const studentId = Number(studentSelect.value);
      courseSelect.innerHTML = `<option value="">Select a course</option>${courses.map((course) => {
        const existing = studentId ? enrollmentFor(studentId, course.id) : null;
        const suffix = existing
          ? existing.status === "dropped"
            ? " — already enrolled (dropped; reactivate below)"
            : ` — already enrolled (${existing.status})`
          : "";
        return `<option value="${course.id}" ${existing ? "disabled" : ""}>${escape(course.title)} (${escape(course.code)})${suffix}</option>`;
      }).join("")}`;
      const unavailable = studentId
        ? courses.filter((course) => enrollmentFor(studentId, course.id)).length
        : 0;
      if (note) {
        note.hidden = !(studentId && courses.length && unavailable === courses.length);
        note.textContent = note.hidden
          ? ""
          : "Every active course already has an enrollment record for this student. Use Reactivate for a dropped row.";
      }
    };

    const render = () => {
      const formBlock = !students.length
        ? '<p class="empty-state">No active students are available. Create or reactivate a student before enrolling.</p>'
        : !courses.length
          ? '<p class="empty-state">No active courses exist yet. <a href="courses.html">Create a course</a> before enrolling students.</p>'
          : `<div class="form-group"><label for="enrollment-student">Student</label><select id="enrollment-student" name="student_id" required><option value="">Select a student</option>${students.map((student) => `<option value="${student.id}">${escape(student.full_name)} (${escape(student.student_code)})</option>`).join("")}</select></div><div class="form-group"><label for="enrollment-course">Course</label><select id="enrollment-course" name="course_id" required><option value="">Select a course</option></select></div><p class="cell-subtext" data-course-note hidden></p>`;
      content.innerHTML = `${formBlock}<div class="table-responsive"><table class="data-table"><thead><tr><th>Student</th><th>Course</th><th>Status</th><th>Action</th></tr></thead><tbody data-enrollment-rows>${enrollmentRowsHtml()}</tbody></table></div>`;
      submitButton.disabled = !students.length || !courses.length;
      updateCourseOptions();
    };

    content.addEventListener("change", (event) => {
      if (event.target.id === "enrollment-student") {
        clearError();
        updateCourseOptions();
      }
    });
    content.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-enrollment-id]");
      if (!button) return;
      const nextStatus = button.dataset.nextStatus;
      if (nextStatus === "dropped" && !window.confirm("Drop this enrollment? Academic history will be preserved.")) return;
      button.disabled = true;
      clearError();
      try {
        const updated = await api.patch(`/admin/enrollments/${button.dataset.enrollmentId}/status?status=${nextStatus}`, {});
        enrollments = enrollments.map((row) => (row.id === updated.id ? updated : row));
        refreshEnrollmentTable();
        updateCourseOptions();
        notify(nextStatus === "dropped" ? "Enrollment dropped." : "Enrollment reactivated.");
        Promise.resolve(onChanged?.()).catch(() => {});
      } catch (error) {
        showError(error.message);
        button.disabled = false;
      }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearError();
      if (!students.length || !courses.length) return;
      if (!form.checkValidity()) return form.reportValidity();
      const data = new FormData(form);
      const studentId = Number(data.get("student_id"));
      const courseId = Number(data.get("course_id"));
      if (!studentId || !courseId) {
        showError("Select both a student and a course.");
        return;
      }
      const existing = enrollmentFor(studentId, courseId);
      if (existing) {
        showError(`This student already has an enrollment in that course (${existing.status}).`);
        return;
      }
      /* Disabling the button while the request is in flight prevents a double
         click from issuing two enrollments. */
      submitButton.disabled = true;
      try {
        const created = await api.post("/admin/enrollments", {
          student_id: studentId,
          course_id: courseId,
          status: "active",
        });
        enrollments.push(created);
        refreshEnrollmentTable();
        updateCourseOptions();
        notify("Student enrolled.");
        Promise.resolve(onChanged?.()).catch(() => {});
      } catch (error) {
        showError(error.message);
      } finally {
        submitButton.disabled = false;
      }
    });
    render();
  }

  /* Student ID is a DISPLAY format for the existing stable profile id. The
     database value (and every enrollment/attendance/mark foreign key) stays
     the original integer; nothing is rewritten or generated by counting rows. */
  function formatStudentId(value) {
    const id = Number(value);
    return Number.isInteger(id) && id > 0
      ? `STU-${String(id).padStart(7, "0")}`
      : "Unavailable";
  }

  async function showStudentDetails(item) {
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog student-details-dialog";
    dialog.setAttribute("aria-labelledby", "student-details-title");
    dialog.innerHTML = `<div class="student-details-shell">
      <header class="student-details-header">
        <div class="student-details-heading">
          <p class="student-details-eyebrow">Read-only record</p>
          <h2 id="student-details-title">Student details</h2>
          <p class="student-details-subtitle" data-details-subtitle>Read-only institutional record.</p>
        </div>
        <button class="student-details-close" type="button" data-cancel aria-label="Close student details">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
        </button>
      </header>
      <div class="student-details-body" data-student-details aria-live="polite">
        <p class="student-details-loading">Loading student details…</p>
      </div>
      <footer class="student-details-footer">
        <span class="student-details-readonly">Read-only record</span>
        <button class="btn btn-outline btn-sm" type="button" data-cancel>Close</button>
      </footer>
    </div>`;
    document.body.append(dialog);

    const root = document.documentElement;
    const opener =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const closeDialog = () => {
      if (dialog.open) dialog.close();
      root.classList.remove("dialog-open");
      dialog.remove();
      if (opener && document.contains(opener)) opener.focus();
    };
    dialog.querySelectorAll("[data-cancel]").forEach((button) => {
      button.addEventListener("click", closeDialog);
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeDialog();
    });
    dialog.addEventListener("close", () => {
      root.classList.remove("dialog-open");
      dialog.remove();
    });

    root.classList.add("dialog-open");
    dialog.showModal();
    dialog.querySelector(".student-details-close").focus();

    const content = dialog.querySelector("[data-student-details]");
    const subtitle = dialog.querySelector("[data-details-subtitle]");
    /* Both are existing admin endpoints: the read-only student projection and
       the enrollment list filtered by the SAME profile id. The details view is
       deliberately not another endpoint. */
    const [studentResult, enrollmentResult] = await Promise.allSettled([
      api.get(`/admin/students/${item.id}`),
      api.get(`/admin/enrollments?student_id=${item.id}&limit=100`),
    ]);

    if (studentResult.status === "rejected") {
      content.innerHTML = `<p class="student-courses-empty" role="alert">Unable to load this student: ${escape(studentResult.reason?.message || "unknown error")}.</p>`;
      return;
    }

    const student = studentResult.value;
    subtitle.textContent = `Read-only institutional record for ${text(student.full_name, "this student")}.`;
    const enrollments = enrollmentResult.status === "fulfilled"
      && Array.isArray(enrollmentResult.value)
      ? enrollmentResult.value
      : null;
    const courseList = enrollments === null
      ? '<p class="student-courses-empty">Enrollment data is unavailable.</p>'
      : enrollments.length
        ? `<ul class="student-course-list">${enrollments.map((enrollment) => `<li><span class="student-course-copy"><span class="cell-title">${escape(enrollment.course_title)}</span><span class="cell-subtext">${escape(enrollment.course_code)}</span></span><span class="badge ${enrollment.status === "active" ? "badge-success" : "badge-warning"}">${escape(enrollment.status)}</span></li>`).join("")}</ul>`
        : '<p class="student-courses-empty">No course enrollments are recorded.</p>';
    const enrollmentCount = enrollments === null ? "—" : enrollments.length;
    const semester = student.current_semester === null
      || student.current_semester === undefined
      || student.current_semester === ""
      ? "Not set"
      : `Semester ${escape(student.current_semester)}`;

    content.innerHTML = `<div class="student-details-identifiers">
        <div class="student-details-identifier"><span class="student-details-label">Student ID</span><strong class="student-id-value">${escape(formatStudentId(student.id))}</strong></div>
        <div class="student-details-identifier"><span class="student-details-label">Roll Number</span><strong class="student-roll-code">${escape(text(student.student_code, "Not set"))}</strong></div>
      </div>
      <dl class="student-details-grid">
        <div class="student-detail wide"><dt>Full name</dt><dd>${escape(text(student.full_name, "Not set"))}</dd></div>
        <div class="student-detail email wide"><dt>Institutional email</dt><dd>${escape(text(student.email, "Not set"))}</dd></div>
        <div class="student-detail"><dt>Phone number</dt><dd>${escape(text(student.phone, "Not set"))}</dd></div>
        <div class="student-detail"><dt>Current semester</dt><dd>${semester}</dd></div>
        <div class="student-detail"><dt>Account status</dt><dd><span class="badge ${student.is_active ? "badge-success" : "badge-warning"}">${student.is_active ? "Active" : "Inactive"}</span></dd></div>
        <div class="student-detail wide"><dt>Degree programme</dt><dd>${escape(text(student.degree_program, "Not set"))}</dd></div>
      </dl>
      <section class="student-details-courses" aria-labelledby="student-courses-title">
        <div class="student-details-courses-head">
          <h3 id="student-courses-title">Enrolled courses</h3>
          <span class="student-details-count" aria-label="${enrollments === null ? "Enrollment count unavailable" : `${enrollments.length} enrolled`}">${enrollmentCount}</span>
        </div>
        ${courseList}
      </section>`;
  }

  async function adminList(resource) {
    const table = document.querySelector(".data-table");
    const search = document.querySelector("[data-table-search]");
    const primaryButton = document.querySelector(".app-topbar button.btn-primary");
    const departmentFilter = document.querySelector("[data-department-filter]");
    const state = { items: [], offset: 0, limit: 20, query: "", department: "", designation: "", facultyStatus: "" };
    /* Form context is loaded once per page: the course form needs real faculty
       ids, and the student form needs the canonical programme catalogue. */
    const formContext = { faculty: [], programmes: [], programmeError: "", departments: [], departmentError: "" };
    if (resource === "courses") {
      try {
        formContext.faculty = await fetchAll("/admin/faculty");
        const catalogue = await api.get("/admin/departments");
        if (isProgrammeCatalogue(catalogue)) {
          formContext.departments = catalogue;
        } else if (
          Array.isArray(catalogue) &&
          catalogue.length > 0 &&
          catalogue.every((d) => typeof d.code === "string" && typeof d.name === "string")
        ) {
          formContext.departments = catalogue;
        } else {
          throw new Error("Malformed department catalogue response.");
        }
      } catch (error) {
        formContext.faculty = [];
        formContext.departments = [];
        formContext.departmentError = describeProgrammeCatalogueError(error);
        console.error("Unable to load the course form catalogue:", error);
      }
    }
    if (resource === "students") {
      try {
        const catalogue = await api.get("/admin/programmes");
        if (!isProgrammeCatalogue(catalogue)) {
          const malformed = new Error("Malformed programme catalogue response.");
          malformed.code = "MALFORMED_PROGRAMMES";
          throw malformed;
        }
        formContext.programmes = catalogue;
      } catch (error) {
        formContext.programmes = [];
        formContext.programmeError = describeProgrammeCatalogueError(error);
        console.error("Unable to load the programme catalogue:", error);
      }
    }
    if (resource === "faculty") {
      try {
        const catalogue = await api.get("/admin/departments");
        if (isProgrammeCatalogue(catalogue)) {
          formContext.departments = catalogue;
        } else if (Array.isArray(catalogue) && catalogue.length > 0 && catalogue.every((d) => typeof d.code === "string" && typeof d.name === "string")) {
          formContext.departments = catalogue;
        } else {
          throw new Error("Malformed department catalogue response.");
        }
      } catch (error) {
        formContext.departments = [];
        formContext.departmentError = describeProgrammeCatalogueError(error);
        console.error("Unable to load the department catalogue:", error);
      }
      try {
        const roster = await fetchAll("/admin/faculty");
        formContext.designations = Array.from(new Set(roster.map((item) => item.designation).filter(Boolean))).sort();
      } catch (error) {
        formContext.designations = [];
      }
    }
    if (document.querySelector(".stats-grid") && adminListStats[resource]) {
      api.get("/admin/reports/summary")
        .then((summary) => renderStatCards(adminListStats[resource](summary)))
        .catch(() => clearStatCards());
    }
    const pager = document.createElement("div");
    pager.className = "row-tight-lg api-pagination";
    pager.innerHTML = '<button type="button" class="btn btn-outline btn-sm" data-prev disabled>Previous</button><span data-page aria-live="polite"></span><button type="button" class="btn btn-outline btn-sm" data-next disabled>Next</button>';
    table.closest(".table-responsive")?.after(pager);

const refreshFacultyStats = async () => {
      try {
        const summary = await api.get("/admin/reports/summary");
        if (adminListStats[resource]) renderStatCards(adminListStats[resource](summary));
      } catch (error) {
        /* Leave the existing counts untouched on failure. */
      }
    };
    /* One place builds the list query, so the table, the pager and the CSV
       export always apply exactly the same search and filters. */
    const listParams = (limit, offset) => {
      const params = new URLSearchParams({ limit, offset });
      if (state.query && adminSearchable.includes(resource)) params.set("search", state.query);
      if (state.department) params.set("department", state.department);
      if (resource === "faculty" && state.designation) params.set("designation", state.designation);
      if (resource === "faculty" && state.facultyStatus) {
        params.set("is_active", state.facultyStatus === "active" ? "true" : "false");
      }
      return params;
    };
    const load = async () => {
      setTableMessage(table, "Loading records…");
      try {
        const data = await api.get(`/admin/${resource}?${listParams(state.limit, state.offset)}`);
        state.items = data.items;
        state.total = data.total;
        renderAdminRows(resource, data.items);
        const title = table?.closest(".card")?.querySelector(".card-title");
        if (title && adminListTitles[resource]) title.textContent = adminListTitles[resource](data.total);
        pager.querySelector("[data-prev]").disabled = state.offset === 0;
        pager.querySelector("[data-next]").disabled = state.offset + state.limit >= data.total;
        pager.querySelector("[data-page]").textContent = data.total ? `Page ${Math.floor(state.offset / state.limit) + 1}` : "";
        setPageMessage(data.total === 0 ? `No ${labelPlural(resource)} found.` : `Showing ${data.offset + 1}–${data.offset + data.items.length} of ${data.total} ${labelPlural(resource)}.`);
      } catch (error) {
        setTableMessage(table, `Unable to load records: ${error.message}`);
        setPageMessage(`Unable to load ${resource}: ${error.message}`, "error");
      }
    };
    /* The degree-programme / department filter is driven by the API's own
       `department` parameter. Students use the canonical catalogue (submitted
       as stable codes) plus any uncatalogued legacy values actually stored,
       which stay visible until an approved normalization changes them. Other
       resources keep listing the values the server stores. */
    if (departmentFilter) {
      try {
        const label = resource === "students" ? "All degree programmes" : "All departments";
        let options = "";
        if (resource === "students" && formContext.programmes.length) {
          const stored = await fetchAll("/admin/students");
          const legacy = Array.from(new Set(stored
            .filter((item) => item.degree_program && !item.programme_code)
            .map((item) => item.degree_program))).sort();
          options = formContext.programmes
            .map((programme) => `<option value="${escape(programme.code)}">${escape(programme.name)}</option>`)
            .join("") + legacy
            .map((value) => `<option value="${escape(value)}">Uncatalogued: ${escape(value)}</option>`)
            .join("");
        } else {
          const stored = await fetchAll(`/admin/${resource}`);
          const values = Array.from(new Set(stored.map((item) => item.degree_program ?? item.department).filter(Boolean))).sort();
          options = values.map((value) => `<option value="${escape(value)}">${escape(value)}</option>`).join("");
        }
        departmentFilter.innerHTML = `<option value="">${label}</option>${options}`;
        departmentFilter.disabled = false;
        departmentFilter.addEventListener("change", () => {
          state.department = departmentFilter.value;
          state.offset = 0;
          load();
        });
      } catch (error) {
        /* The filter stays disabled rather than pretending to filter. */
      }
    }
    /* The export writes the records the API actually returns - it pages
       through the endpoint instead of exporting the visible slice only. */
    const exportButton = document.querySelector("[data-export-csv]");
    if (exportButton) {
      exportButton.disabled = false;
      exportButton.addEventListener("click", async () => {
        exportButton.disabled = true;
        try {
          const items = [];
          let offset = 0;
          for (let page = 0; page < 200; page += 1) {
            const batch = await api.get(`/admin/${resource}?${listParams(100, offset)}`);
            items.push(...batch.items);
            offset += batch.items.length;
            if (!batch.items.length || offset >= batch.total) break;
          }
          const rows = [adminExportHeaders[resource]].concat(items.map(adminExportRows[resource]));
          downloadCsv(`campus360-${resource}.csv`, rows);
          setPageMessage(`Exported ${items.length} ${resource} record${items.length === 1 ? "" : "s"}.`);
        } catch (error) {
          setPageMessage(`Unable to export ${resource}: ${error.message}`, "error");
        } finally {
          exportButton.disabled = false;
        }
      });
    }
    if (primaryButton) {
      primaryButton.disabled = false;
      primaryButton.addEventListener("click", () => showAdminForm(resource, null, async (values) => {
        await api.post(`/admin/${resource}`, values);
        await load();
        await refreshFacultyStats();
        notify(`${labelFor(resource)} created.`);
      }, formContext));
    }
    if (resource === "students") {
      const enrollmentButton = document.createElement("button");
      enrollmentButton.type = "button";
      enrollmentButton.className = "btn btn-outline btn-sm";
      enrollmentButton.textContent = "Manage Enrollments";
      primaryButton?.after(enrollmentButton);
      enrollmentButton.addEventListener("click", () => showEnrollmentDialog(load));

      const importButton = document.querySelector("[data-import-students]");
      if (importButton) {
        importButton.disabled = false;
        importButton.addEventListener("click", () => showImportDialog("students", load));
      }
    }
    if (resource === "faculty") {
      const importButton = document.querySelector("[data-import-faculty]");
      if (importButton) {
        importButton.disabled = false;
        importButton.addEventListener("click", () => showImportDialog("faculty", load));
      }
    }
    table.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-record-id]");
      if (!button) return;
      const id = Number(button.dataset.recordId);
      const item = state.items.find((record) => record.id === id);
      if (!item) return;
      try {
        if (button.dataset.action === "details") {
          showStudentDetails(item);
          return;
        }
        if (button.dataset.action === "edit") {
          showAdminForm(resource, item, async (values) => {
            await api.patch(`/admin/${resource}/${id}`, values);
            await load();
            await refreshFacultyStats();
            notify(`${labelFor(resource)} updated.`);
          }, formContext);
        } else if (button.dataset.action === "activate") {
          if (!window.confirm(`Activate ${item.full_name || item.title}?`)) return;
          await api.patch(`/admin/${resource}/${id}`, {is_active: true});
          await load();
          await refreshFacultyStats();
          notify(`${labelFor(resource)} activated.`);
        } else if (button.dataset.action === "deactivate") {
          if (!window.confirm(`Deactivate ${item.full_name || item.title}? This preserves academic history.`)) return;
          await api.delete(`/admin/${resource}/${id}`);
          await load();
          await refreshFacultyStats();
          notify(`${labelFor(resource)} deactivated.`);
        } else if (button.dataset.action === "archive") {
          const endpoint = item.status === "active" ? "archive" : "publish";
          if (!window.confirm(`${endpoint === "archive" ? "Archive" : "Publish"} this announcement?`)) return;
          await api.post(`/admin/announcements/${id}/${endpoint}`, {});
          await load();
          notify(endpoint === "archive" ? "Announcement archived." : "Announcement published.");
        }
      } catch (error) {
        notify(`Could not save changes: ${error.message}`, "error");
      }
    });
    if (search) {
      let debounce;
      search.addEventListener("input", () => {
        window.clearTimeout(debounce);
        debounce = window.setTimeout(() => {
          state.query = search.value.trim();
          state.offset = 0;
          load();
        }, 250);
      });
    }
    pager.addEventListener("click", (event) => {
      if (event.target.matches("[data-prev]") && state.offset > 0) {
        state.offset = Math.max(0, state.offset - state.limit);
        load();
      }
      if (event.target.matches("[data-next]") && state.offset + state.limit < state.total) {
        state.offset += state.limit;
        load();
      }
    });

    /* Faculty-specific filter toolbar (custom markup on faculty.html). */
    if (resource === "faculty") {
      const facultySearch = document.getElementById("faculty-search");
      const facultyDept = document.getElementById("faculty-dept");
      const facultyDesig = document.getElementById("faculty-designation");
      const facultyState = document.getElementById("faculty-state");
      const facultyReset = document.getElementById("faculty-reset");

      if (facultyDept) {
        facultyDept.innerHTML = '<option value="">All Departments</option>';
        (formContext.departments || []).forEach((dept) => {
          const value = typeof dept === "string" ? dept : (dept.name || dept.code || "");
          if (!value) return;
          const opt = document.createElement("option");
          opt.value = value;
          opt.textContent = value;
          facultyDept.appendChild(opt);
        });
      }
      if (facultyDesig) {
        /* Same designation list the create/edit form offers (loaded above). */
        facultyDesig.innerHTML = '<option value="">All Designations</option>';
        (formContext.designations || []).forEach((value) => {
          const opt = document.createElement("option");
          opt.value = value;
          opt.textContent = value;
          facultyDesig.appendChild(opt);
        });
      }

      const triggerFacultyFilters = () => {
        state.query = facultySearch ? facultySearch.value.trim() : state.query;
        state.department = facultyDept ? facultyDept.value : state.department;
        state.designation = facultyDesig ? facultyDesig.value : state.designation;
        state.facultyStatus = facultyState ? facultyState.value : state.facultyStatus;
        state.offset = 0;
        load();
      };

      if (facultySearch) {
        let debounce;
        facultySearch.addEventListener("input", () => {
          window.clearTimeout(debounce);
          debounce = window.setTimeout(triggerFacultyFilters, 300);
        });
      }
      if (facultyDept) facultyDept.addEventListener("change", triggerFacultyFilters);
      if (facultyDesig) facultyDesig.addEventListener("change", triggerFacultyFilters);
      if (facultyState) facultyState.addEventListener("change", triggerFacultyFilters);
      if (facultyReset) {
        facultyReset.addEventListener("click", () => {
          if (facultySearch) facultySearch.value = "";
          if (facultyDept) facultyDept.value = "";
          if (facultyDesig) facultyDesig.value = "";
          if (facultyState) facultyState.value = "";
          state.query = "";
          state.department = "";
          state.designation = "";
          state.facultyStatus = "";
          state.offset = 0;
          load();
        });
      }
    }

    load();
  }

  function replaceTableHeaders(table, headers) {
    const row = table?.querySelector("thead tr");
    if (row) row.innerHTML = headers.map((header) => `<th>${escape(header)}</th>`).join("");
  }

  /* Sets a stat card's label, value AND supporting text together, so a tile
     can never carry a real number under a label that describes something
     else. Shared by the admin, faculty and student dashboards. */
  function renderStatCards(items) {
    const cards = document.querySelectorAll(".stats-grid .stat-card");
    items.forEach((item, index) => {
      const card = cards[index];
      if (!card) return;
      const label = card.querySelector(".stat-label");
      const value = card.querySelector(".stat-value");
      const meta = card.querySelector(".stat-meta");
      if (label) label.textContent = item.label;
      if (value) value.textContent = item.value;
      if (meta) meta.textContent = item.meta || "";
    });
  }

  /* Used when a request fails: every tile falls back to an em dash and an
     honest "Unavailable" caption instead of keeping a stale figure. */
  function clearStatCards() {
    document.querySelectorAll(".stats-grid .stat-value").forEach((node) => { node.textContent = "—"; });
    document.querySelectorAll(".stats-grid .stat-meta").forEach((node) => { node.textContent = "Unavailable"; });
  }

  function assignmentStatus(item) {
    const known = { graded: "Graded", submitted: "Submitted", returned: "Returned" };
    return known[String(item.submission_status || "").toLowerCase()] || "Not submitted";
  }

  function renderStudentAssignments(table, assignments) {
    replaceTableHeaders(table, ["Course", "Assignment", "Due", "Submission", "Score", "Availability"]);
    if (!assignments.length) return setTableMessage(table, "No active assignments are available.");
    table.querySelector("tbody").innerHTML = assignments.map((item) => `<tr><td><strong>${escape(item.course_code)}</strong><span class="cell-subtext">${escape(item.course_title)}</span></td><td><span class="cell-title">${escape(item.title)}</span><span class="cell-subtext">${escape(item.description)}</span></td><td>${formatDate(item.due_at)}</td><td>${escape(assignmentStatus(item))}</td><td>${item.score === null ? "—" : `${escape(item.score)} / ${escape(item.max_score)}`}</td><td>Viewing only</td></tr>`).join("");
  }

  async function studentPage() {
    const table = document.querySelector(".data-table");
    if (table) setTableMessage(table, "Loading your academic data…");
    document.querySelectorAll(".nav-counter").forEach((node) => { node.textContent = "—"; });
    try {
      const profile = await api.get("/student/me/profile");
      document.querySelectorAll(".user-name").forEach((node) => { node.textContent = profile.full_name; });
      if (page === "dashboard.html") {
        /* Field-for-field against the student API:
             /student/me/dashboard  -> DashboardResponse
             /student/me/timetable  -> list[MyTimetableEntryResponse]
             /student/me/assignments-> list[StudentAssignmentResponse]
             /student/me/marks/summary -> MarksSummaryResponse
           The marks summary is optional: a failure there must not blank the
           rest of the dashboard, it only costs the average-marks figure. */
        const [data, timetable, assignments, marks] = await Promise.all([
          api.get("/student/me/dashboard"),
          api.get("/student/me/timetable"),
          api.get("/student/me/assignments"),
          api.get("/student/me/marks/summary").catch(() => null),
        ]);
        const today = new Intl.DateTimeFormat("en-US", { weekday: "long" }).format(new Date()).toLowerCase();
        const todaysClasses = timetable.filter((entry) => text(entry.day_of_week).toLowerCase() === today);
        const upcoming = data.upcoming_assignments || [];
        const pendingAssignments = assignments.filter((item) => !item.submission_status || item.submission_status === "returned");
        const futureAssignments = assignments.filter((item) => item.due_at && new Date(item.due_at).getTime() >= Date.now());
        const components = (marks?.courses || []).flatMap((course) => course.components || []);
        const scored = components.reduce((sum, item) => sum + item.score, 0);
        const scoredMax = components.reduce((sum, item) => sum + item.max_score, 0);
        const averageMarks = components.length && scoredMax > 0 ? Math.round((scored * 1000) / scoredMax) / 10 : null;

        const banner = document.querySelector(".welcome-banner");
        if (banner) {
          const heading = banner.querySelector("h2");
          const summary = banner.querySelector("p");
          if (heading) heading.textContent = `Welcome, ${profile.full_name}.`;
          if (summary) summary.textContent = `${data.enrolled_courses_count} active course${data.enrolled_courses_count === 1 ? "" : "s"} • ${todaysClasses.length} class${todaysClasses.length === 1 ? "" : "es"} scheduled today • ${pendingAssignments.length} assignment${pendingAssignments.length === 1 ? "" : "s"} awaiting submission.`;
        }
        document.querySelectorAll(".user-role-label").forEach((node) => {
          node.textContent = `${text(profile.degree_program, "Program not recorded")} • Semester ${text(profile.current_semester, "not recorded")}`;
        });
        const avatar = document.querySelector(".user-avatar");
        if (avatar) avatar.textContent = (profile.full_name || "").trim().charAt(0).toUpperCase() || "—";
        const semester = document.querySelector("[data-semester-context]");
        if (semester) semester.textContent = `Semester ${text(profile.current_semester, "not recorded")}`;
        document.querySelectorAll(".nav-counter").forEach((node) => { node.textContent = String(pendingAssignments.length); });

        renderStatCards([
          { label: "Overall attendance", value: data.overall_attendance_percent === null ? "—" : `${data.overall_attendance_percent}%`, meta: "Based on recorded sessions" },
          { label: "Pending assignments", value: pendingAssignments.length, meta: `Of ${assignments.length} active assignment${assignments.length === 1 ? "" : "s"}` },
          { label: "Average marks", value: averageMarks === null ? "—" : `${averageMarks}%`, meta: components.length ? `Across ${components.length} recorded component${components.length === 1 ? "" : "s"}` : "No marks recorded yet" },
          { label: "Registered credits", value: data.total_credits, meta: `Across ${data.enrolled_courses_count} active course${data.enrolled_courses_count === 1 ? "" : "s"}` },
        ]);

        const schedule = document.querySelector(".schedule-list");
        if (schedule) {
          schedule.innerHTML = todaysClasses.length
            ? todaysClasses.map((entry) => {
                const start = formatTimeParts(entry.start_time);
                const where = [entry.faculty_name, entry.room].filter(Boolean).map(escape).join(" • ");
                return `<div class="schedule-row"><div class="schedule-time-badge"><strong>${start.time}</strong><small>${start.meridiem}</small></div><div class="fill"><strong class="cell-title">${escape(entry.course_title)}</strong><span class="cell-subtext">${escape(entry.course_code)} • ${escape(formatTimeLabel(entry.start_time))} – ${escape(formatTimeLabel(entry.end_time))}</span>${where ? `<span class="cell-subtext">${where}</span>` : ""}</div></div>`;
              }).join("")
            : '<p class="empty-state">No classes are scheduled for today.</p>';
        }

        const feed = document.querySelector(".activity-list");
        if (feed) {
          const announcements = data.recent_announcements || [];
          feed.innerHTML = announcements.length
            ? announcements.map((item) => `<div class="activity-item"><div class="activity-icon"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8.5a6 6 0 0 0-12 0c0 6-2.5 8-2.5 8h17S18 14.5 18 8.5z"/><path d="M13.7 20.5a2 2 0 0 1-3.4 0"/></svg></div><div class="activity-details"><span class="activity-title">${escape(item.title)}</span><span class="activity-meta">${escape(item.category)} • ${formatDate(item.published_at)}</span></div></div>`).join("")
            : '<div class="activity-item"><div class="activity-details"><span class="activity-title">No recent announcements</span><span class="activity-meta">Nothing has been published for students yet.</span></div></div>';
        }

        if (table) {
          replaceTableHeaders(table, ["Course", "Assignment", "Due date", "Submission", "Details"]);
          if (!upcoming.length) setTableMessage(table, "No upcoming assignment deadlines are available.");
          else table.querySelector("tbody").innerHTML = upcoming.map((item) => `<tr><td><strong>${escape(item.course_code)}</strong><span class="cell-subtext">${escape(item.course_title)}</span></td><td><span class="cell-title">${escape(item.title)}</span>${item.description ? `<span class="cell-subtext">${escape(item.description)}</span>` : ""}</td><td>${formatDate(item.due_at)}</td><td>${submissionBadge(item.submission_status)}</td><td><a class="btn btn-outline btn-sm" href="assignments.html">View</a></td></tr>`).join("");
          const note = document.querySelector("[data-table-note]");
          if (note) {
            note.textContent = !upcoming.length
              ? "No upcoming deadlines to show."
              : futureAssignments.length > upcoming.length
                ? `Showing the ${upcoming.length} nearest of ${futureAssignments.length} upcoming deadlines.`
                : `Showing all ${upcoming.length} upcoming deadline${upcoming.length === 1 ? "" : "s"}.`;
            note.hidden = false;
          }
        }
      } else if (page === "attendance.html") {
        const data = await api.get("/student/me/attendance");
        const total = data.courses.reduce((sum, course) => sum + course.total_sessions, 0);
        const present = data.courses.reduce((sum, course) => sum + course.present_count, 0);
        const late = data.courses.reduce((sum, course) => sum + course.late_count, 0);
        const percent = data.overall_percent === null ? "—" : `${data.overall_percent}%`;
        const meter = document.querySelector(".radial-percentage strong");
        if (meter) meter.textContent = percent;
        const dial = document.querySelector(".radial-percentage");
        if (dial) dial.setAttribute("aria-label", data.overall_percent === null ? "Attendance percentage unavailable" : `${data.overall_percent}% attendance`);
        const context = document.querySelector("[data-attendance-context]");
        if (context) context.textContent = `Semester ${text(profile.current_semester, "not recorded")} • ${percent} overall`;
        const eligibility = document.querySelector(".attendance-eligibility");
        if (eligibility) eligibility.textContent = data.overall_percent === null ? "No attendance sessions recorded" : data.overall_percent >= 75 ? "At or above 75% attendance" : "Below 75% attendance";
        const attendanceTitle = document.querySelector(".attendance-status-title");
        if (attendanceTitle) attendanceTitle.textContent = data.overall_percent === null ? "Attendance status unavailable" : data.overall_percent >= 75 ? "Attendance status: on track" : "Attendance status: review needed";
        const attendanceNote = document.querySelector(".attendance-status-note");
        if (attendanceNote) attendanceNote.textContent = total ? `${present} present and ${late} late marks across ${total} recorded sessions. Attendance requirements are determined by your institution.` : "No attendance sessions have been recorded for your enrolled courses.";
        renderStatCards([{ label: "Total sessions", value: total, meta: "Recorded across enrolled courses" }, { label: "Present", value: present, meta: "Marked present" }, { label: "Late", value: late, meta: "Marked late" }, { label: "Overall attendance", value: percent, meta: "Based on recorded sessions" }]);
        if (table) {
          replaceTableHeaders(table, ["Course", "Faculty", "Present / Total", "Percentage", "Status"]);
          if (!data.courses.length) setTableMessage(table, "No attendance data is available.");
          else table.querySelector("tbody").innerHTML = data.courses.map((course) => `<tr><td><span class="cell-title">${escape(course.course_title)}</span><span class="cell-subtext">${escape(course.course_code)}</span></td><td>${escape(course.faculty_name)}</td><td><strong>${course.present_count}</strong> / ${course.total_sessions}</td><td>${course.attendance_percent === null ? "—" : `${course.attendance_percent}%`}</td><td>${escape(course.status)}</td></tr>`).join("");
        }
      } else if (page === "assignments.html") {
        const data = await api.get("/student/me/assignments");
        const submitted = data.filter((item) => item.submission_status).length;
        const graded = data.filter((item) => item.submission_status === "graded").length;
        const pending = data.length - submitted;
        renderStatCards([{ label: "Not submitted", value: pending, meta: "Submission upload is not available in this API" }, { label: "Submitted", value: submitted, meta: `${graded} graded` }, { label: "Active assignments", value: data.length, meta: "Visible in your enrolled courses" }, { label: "Graded", value: graded, meta: "Scores shown below when released" }]);
        document.querySelectorAll(".nav-counter").forEach((node) => { node.textContent = String(pending); });
        const pendingContext = document.querySelector("[data-pending-context]");
        if (pendingContext) {
          pendingContext.textContent = !data.length
            ? "No active assignments"
            : pending
              ? `${pending} awaiting submission`
              : "All submissions recorded";
        }
        if (table) renderStudentAssignments(table, data);
      } else if (page === "marks-results.html") {
        const data = await api.get("/student/me/marks/summary");
        const complete = data.courses.filter((course) => course.status === "Complete").length;
        renderStatCards([{ label: "Courses with results", value: complete, meta: "All required components recorded" }, { label: "Courses in progress", value: data.courses.length - complete, meta: "No GPA is calculated by this API" }, { label: "Course results", value: data.courses.length, meta: "Currently enrolled courses" }, { label: "Transcript", value: "—", meta: "Historical transcript data is unavailable" }]);
        /* The export writes the student's OWN recorded components - the API
           has no official transcript, so the file says exactly what it is. */
        const exportButton = document.querySelector("[data-export-csv]");
        if (exportButton) {
          exportButton.disabled = false;
          exportButton.addEventListener("click", () => {
            const rows = [["Course code", "Course title", "Component", "Score", "Max score", "Percentage", "Grade", "Recorded at"]];
            data.courses.forEach((course) => course.components.forEach((component) => rows.push([
              course.course_code,
              course.course_title,
              component.component,
              component.score,
              component.max_score,
              component.percentage,
              component.grade,
              component.recorded_at,
            ])));
            downloadCsv("campus360-my-results.csv", rows);
          });
        }
        if (table) {
          replaceTableHeaders(table, ["Course", "Credits", "Recorded components", "Overall result", "Status"]);
          if (!data.courses.length) setTableMessage(table, "No course marks are available.");
          else table.querySelector("tbody").innerHTML = data.courses.map((course) => `<tr><td><strong>${escape(course.course_code)}</strong><span class="cell-subtext">${escape(course.course_title)}</span></td><td>${course.credits}</td><td>${course.components.length}</td><td>${course.overall_percentage === null ? "—" : `${course.overall_percentage}% (${escape(course.overall_grade)})`}</td><td>${escape(course.status)}</td></tr>`).join("");
        }
      } else if (page === "timetable.html") {
        const data = await api.get("/student/me/timetable");
        const title = document.querySelector(".card-title");
        if (title) title.textContent = `${text(profile.degree_program, "Your program")} • Semester ${text(profile.current_semester, "—")}`;
        const scheduleSummary = document.querySelector(".row-center-sm");
        if (scheduleSummary) scheduleSummary.innerHTML = `<span class="badge badge-primary">${data.length} scheduled session${data.length === 1 ? "" : "s"}</span>`;
        const grid = document.querySelector(".timetable-grid");
        if (grid) {
          const days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
          grid.innerHTML = days.map((day) => {
            const entries = data.filter((entry) => text(entry.day_of_week).toLowerCase() === day);
            const label = day[0].toUpperCase() + day.slice(1);
            /* The day column, header and class card classes come from
               css/student.css - the API has no session-type field, so no
               lecture/lab accent is invented. */
            return `<div class="day-column"><div class="day-header"><span class="day-title">${label}</span><span class="day-count">${entries.length ? `${entries.length} session${entries.length === 1 ? "" : "s"}` : "No sessions"}</span></div>${entries.length ? entries.map((entry) => {
              const details = [entry.course_code, entry.room, entry.faculty_name].filter(Boolean).map(escape).join(" • ");
              return `<div class="class-card"><div class="class-time"><span>${escape(formatTimeLabel(entry.start_time))} – ${escape(formatTimeLabel(entry.end_time))}</span></div><span class="class-subject">${escape(entry.course_title)}</span><div class="class-meta"><span>${details}</span></div></div>`;
            }).join("") : '<p class="empty-state">No classes</p>'}</div>`;
          }).join("");
        }
      } else if (page === "announcements.html") {
        const data = await api.get("/student/me/announcements");
        const wrapper = document.querySelector(".content-wrapper");
        if (!wrapper) return;

        /* Only the page body is replaced; the topbar (and its filter host)
           lives outside .content-wrapper and stays put. */
        Array.from(wrapper.children).forEach((node) => node.remove());

        const section = document.createElement("section");
        section.className = "announcement-list";
        wrapper.append(section);

        function announcementMarkup(item) {
          return `<article class="announcement-card"><div class="announcement-meta"><span class="badge badge-secondary">${escape(item.category)}</span><span class="meta-muted">Published: ${formatDate(item.published_at)}</span></div><h2 class="announcement-title">${escape(item.title)}</h2><p class="announcement-desc">${escape(item.body)}</p><div class="announcement-footer"><span>Audience: ${escape(item.audience)}</span></div></article>`;
        }

        /* The tabs are built from the categories the API actually returned.
           A fixed list (the old "Academic / Examinations / Events" markup)
           invented categories that no record could carry, so the tabs never
           filtered anything. */
        const categories = Array.from(new Set(data.map((item) => text(item.category, "")).filter(Boolean)));
        const filters = [{ label: "All notices", category: null }].concat(
          categories.map((category) => ({ label: category, category })),
        );
        const filterHost = document.querySelector("[data-announcement-filter]");

        function renderAnnouncements(category) {
          const visible = category === null ? data : data.filter((item) => item.category === category);
          section.innerHTML = visible.length
            ? visible.map(announcementMarkup).join("")
            : `<p class="empty-state">No announcements in this category.</p>`;
        }

        if (filterHost) {
          if (data.length && categories.length > 1) {
            filterHost.innerHTML = filters.map((filter, index) => `<button type="button" class="filter-tab${index === 0 ? " active" : ""}" data-filter-index="${index}" aria-pressed="${index === 0}">${escape(filter.label)}</button>`).join("");
            filterHost.addEventListener("click", (event) => {
              const button = event.target.closest("[data-filter-index]");
              if (!button) return;
              const index = Number(button.dataset.filterIndex);
              filterHost.querySelectorAll("[data-filter-index]").forEach((node) => {
                const selected = node === button;
                node.classList.toggle("active", selected);
                node.setAttribute("aria-pressed", String(selected));
              });
              renderAnnouncements(filters[index].category);
            });
          } else {
            /* One category (or none) means there is nothing to filter. */
            filterHost.remove();
          }
        }

        renderAnnouncements(null);
      } else if (page === "academic-records.html") {
        const data = await api.get("/student/me/marks/summary");
        /* The dossier items are matched BY LABEL, not by position: the page
           carries six items and a positional mapping silently wrote the
           advisor note into "Current Status" and left the fabricated credit
           line untouched. */
        const infoValues = {
          "Full Legal Name": profile.full_name,
          "Enrollment Number": profile.student_code,
          "Academic Program": text(profile.degree_program, "Program not recorded"),
          "Current Status": `Semester ${text(profile.current_semester, "not recorded")}`,
          "Academic Advisor": "Advisor information is not exposed by the current API.",
          "Total Earned Credits": "Cumulative credits and GPA are not exposed by the current API.",
        };
        document.querySelectorAll(".info-item").forEach((item) => {
          const label = item.querySelector("label")?.textContent.trim();
          const value = item.querySelector("span");
          if (value && label && infoValues[label] !== undefined) value.textContent = infoValues[label];
        });
        /* Account state comes from the profile; the old badge claimed
           "Active Full-Time", which the schema does not record. */
        const accountStatus = document.querySelector("[data-account-status]");
        if (accountStatus) {
          accountStatus.textContent = profile.is_active ? "Active account" : "Inactive account";
          accountStatus.className = `badge ${profile.is_active ? "badge-success" : "badge-warning"}`;
        }
        const heading = document.querySelector(".card-title");
        const subtitle = document.querySelector(".card-subtitle");
        if (heading) heading.textContent = "Current Course Result Summary";
        if (subtitle) subtitle.textContent = "Not an official historical transcript";
        if (table) {
          replaceTableHeaders(table, ["Course", "Credits", "Recorded components", "Overall result", "Status"]);
          if (!data.courses.length) setTableMessage(table, "No course result data is available.");
          else table.querySelector("tbody").innerHTML = data.courses.map((course) => `<tr><td><strong>${escape(course.course_code)}</strong><span class="cell-subtext">${escape(course.course_title)}</span></td><td>${course.credits}</td><td>${course.components.length}</td><td>${course.overall_percentage === null ? "—" : `${course.overall_percentage}% (${escape(course.overall_grade)})`}</td><td>${escape(course.status)}</td></tr>`).join("");
        }
      }
    } catch (error) {
      if (table) setTableMessage(table, `Unable to load your data: ${error.message}`);
      setPageMessage(`Unable to load your data: ${error.message}`, "error");
      clearStatCards();
      resetStudentPage();
    }
  }

  /* A failed request must leave an honest page behind: no stale figure, no
     "loading…" text that suggests data is still on its way. */
  function resetStudentPage() {
    if (page === "dashboard.html") return resetStudentDashboard();
    const grid = document.querySelector(".timetable-grid");
    if (grid) grid.innerHTML = '<p class="empty-state">Your timetable is unavailable right now.</p>';
    const announcements = document.querySelector(".announcement-list");
    if (announcements) announcements.innerHTML = '<p class="empty-state">Announcements are unavailable right now.</p>';
    document.querySelectorAll(".info-item span").forEach((node) => { node.textContent = "—"; });
    const note = document.querySelector("[data-table-note]");
    if (note) {
      note.textContent = "Your data could not be loaded.";
      note.hidden = false;
    }
  }

  /* A failed dashboard request must not leave the last known figures (or the
     markup placeholders) standing in as if they were real academic records. */
  function resetStudentDashboard() {
    document.querySelectorAll(".stats-grid .stat-value").forEach((node) => { node.textContent = "—"; });
    document.querySelectorAll(".stats-grid .stat-meta").forEach((node) => { node.textContent = "Unavailable"; });
    const schedule = document.querySelector(".schedule-list");
    if (schedule) schedule.innerHTML = '<p class="empty-state">Today\'s schedule is unavailable right now.</p>';
    const feed = document.querySelector(".activity-list");
    if (feed) feed.innerHTML = '<div class="activity-item"><div class="activity-details"><span class="activity-title">Recent updates are unavailable right now.</span></div></div>';
    const note = document.querySelector("[data-table-note]");
    if (note) {
      note.textContent = "Upcoming deadlines could not be loaded.";
      note.hidden = false;
    }
  }

  async function adminReports() {
    const table = document.querySelector(".data-table");
    if (table) setTableMessage(table, "Loading report data…");
    try {
      const [summary, attendance, enrollments] = await Promise.all([
        api.get("/admin/reports/summary"),
        api.get("/admin/reports/attendance"),
        api.get("/admin/reports/enrollments"),
      ]);
      renderStatCards([
        { label: "Institution attendance rate", value: summary.institution_attendance_rate === null ? "—" : `${summary.institution_attendance_rate}%`, meta: "Present or late across all records" },
        { label: "Pass percentage", value: summary.pass_percentage === null ? "—" : `${summary.pass_percentage}%`, meta: "Graded students at or above 40% overall" },
        { label: "Active enrollments", value: summary.total_enrollments, meta: "Enrollment records with active status" },
        { label: "Active courses", value: summary.active_courses, meta: `${summary.total_courses} in the course catalogue` },
      ]);
      renderAttendanceChart(attendance);
      renderUnavailableChart(1, "Grade distribution is not exposed by the current API, so no distribution is shown.");
      const note = document.querySelector("[data-report-note]");
      if (note) {
        note.textContent = summary.notes?.length ? summary.notes.join(" • ") : "All report metrics are backed by live API data.";
        note.hidden = false;
      }
      if (table) {
        table.querySelector("tbody").innerHTML = attendance.courses.length
          ? attendance.courses.map((course) => `<tr><td><strong>${escape(course.course_code)}</strong></td><td>${escape(course.course_title)}</td><td>${course.total_records}</td><td>${course.attendance_percentage ?? "—"}%</td><td>${enrollments.courses.find((item) => item.course_id === course.course_id)?.enrolled_students ?? 0}</td></tr>`).join("")
          : `<tr><td colspan="${table.querySelectorAll("thead th").length}">No attendance records are available.</td></tr>`;
      }
      const exportButton = document.querySelector("[data-report-export]");
      if (exportButton) {
        exportButton.disabled = false;
        exportButton.addEventListener("click", () => downloadCsv("campus360-master-report.csv", buildMasterReportRows(summary, attendance, enrollments)));
      }
    } catch (error) {
      if (table) setTableMessage(table, `Unable to load reports: ${error.message}`);
      setPageMessage(`Unable to load reports: ${error.message}`, "error");
      clearStatCards();
      renderUnavailableChart(0, "Attendance report data is unavailable right now.");
      renderUnavailableChart(1, "Grade distribution is not exposed by the current API, so no distribution is shown.");
    }
  }

  /* The attendance report is per COURSE, so the chart is rebuilt from those
     rows. Courses without a single recorded attendance keep a null
     percentage and are left out rather than drawn as 0%. */
  function renderAttendanceChart(attendance) {
    const chart = document.querySelectorAll(".css-bar-chart")[0];
    if (!chart) return;
    const scored = attendance.courses.filter((course) => course.attendance_percentage !== null);
    const shown = scored.slice(0, 8);
    chart.innerHTML = shown.length
      ? shown.map((course) => `<div class="chart-bar-group"><span class="chart-bar-value">${course.attendance_percentage}%</span><div class="chart-bar-fill" style="height: ${course.attendance_percentage}%;"></div><span class="chart-bar-label">${escape(course.course_code)}</span></div>`).join("")
      : '<p class="empty-state">No attendance records are available yet.</p>';
    const card = chart.closest(".card");
    const title = card?.querySelector(".card-title");
    if (title) title.textContent = "Course Attendance Rate (%)";
    const legend = card?.querySelectorAll(".legend-item span");
    if (legend?.length) legend[legend.length - 1].textContent = scored.length > shown.length
      ? `Showing the ${shown.length} highest of ${scored.length} courses with recorded attendance`
      : "Attendance rate per course (present or late counts as attended)";
  }

  function renderUnavailableChart(index, message) {
    const chart = document.querySelectorAll(".css-bar-chart")[index];
    if (!chart) return;
    chart.innerHTML = `<p class="empty-state">${escape(message)}</p>`;
    const card = chart.closest(".card");
    const legend = card?.querySelectorAll(".legend-item span");
    if (legend?.length) legend[legend.length - 1].textContent = "No API source for this metric";
  }

  /* The master report is built ONLY from the three report payloads that were
     just loaded, so the exported file can never contain a placeholder. */
  function buildMasterReportRows(summary, attendance, enrollments) {
    const rows = [["Campus360 master report"], ["Generated", new Date().toISOString()], []];
    rows.push(["Institution summary", "Value"]);
    rows.push(["Active students", summary.active_students]);
    rows.push(["Total students", summary.total_students]);
    rows.push(["Active faculty", summary.active_faculty]);
    rows.push(["Total faculty", summary.total_faculty]);
    rows.push(["Active courses", summary.active_courses]);
    rows.push(["Total courses", summary.total_courses]);
    rows.push(["Active enrollments", summary.total_enrollments]);
    rows.push(["Open sessions", summary.active_sessions]);
    rows.push(["Institution attendance rate", summary.institution_attendance_rate === null ? "" : `${summary.institution_attendance_rate}%`]);
    rows.push(["Pass percentage", summary.pass_percentage === null ? "" : `${summary.pass_percentage}%`]);
    rows.push([]);
    rows.push(["Attendance by course", "Title", "Records", "Present", "Late", "Absent", "Attendance %"]);
    attendance.courses.forEach((course) => rows.push([
      course.course_code,
      course.course_title,
      course.total_records,
      course.present_count,
      course.late_count,
      course.absent_count,
      course.attendance_percentage === null ? "" : course.attendance_percentage,
    ]));
    rows.push([]);
    rows.push(["Enrollments by course", "Title", "Department", "Enrolled students"]);
    enrollments.courses.forEach((course) => rows.push([
      course.course_code,
      course.course_title,
      course.department || "Unassigned",
      course.enrolled_students,
    ]));
    rows.push([]);
    rows.push(["Enrollments by department", "Active enrollments"]);
    Object.entries(enrollments.by_department).forEach(([department, count]) => rows.push([department, count]));
    if (summary.notes?.length) {
      rows.push([]);
      rows.push(["Notes"]);
      summary.notes.forEach((entry) => rows.push([entry]));
    }
    return rows;
  }

  /* Every cell is quoted, and a cell that a spreadsheet would run as a
     formula (leading = + - @ tab or carriage return) is neutralised with a
     leading apostrophe. The BOM lets Excel detect UTF-8 names correctly. */
  function downloadCsv(filename, rows) {
    const csv = rows.map((row) => row.map((value) => {
      let text = String(value ?? "");
      if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
      return `"${text.replaceAll('"', '""')}"`;
    }).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  /* An API timestamp as the local "YYYY-MM-DDTHH:MM" a datetime-local input
     expects (toISOString() alone would show the UTC wall-clock time). */
  function toLocalDateTimeInput(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  function showFacultyForm(kind, current, onSave, context) {
    const editing = Boolean(current);
    const hints = context || {};
    /* The faculty API has no roster or course-catalogue read, so the ids are
       typed rather than picked from a list the server cannot produce. The
       hints list the ids this account IS known to own, so the admin can be
       corrected instead of guessed. */
    const fields = kind === "assignment"
      ? [["course_id", "Course ID (a course you lead)", "number", true, [], null, hints.courses], ["title", "Title", "text", true], ["description", "Description", "textarea"], ["due_at", "Due date and time", "datetime-local", true], ["weightage_percent", "Weightage percent", "number", false, [], { min: "0.01", max: "100" }], ["max_score", "Maximum score", "number", true, [], { min: "0.01", max: "999.99" }]]
      : [["student_id", "Student profile ID", "number", true, [], null, hints.students], ["course_id", "Course ID (a course you lead)", "number", true, [], null, hints.courses], ["component", "Component", "select", true, ["internal", "midterm", "theory", "practical"]], ["score", "Score", "number", true, [], { min: "0", max: "100" }], ["max_score", "Maximum score", "number", true, [], { min: "0.01", max: "100" }]];
    const immutable = editing ? (kind === "assignment" ? ["course_id"] : ["student_id", "course_id", "component"]) : [];
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog";
    dialog.innerHTML = `<form method="dialog"><h2>${editing ? "Edit" : "Create"} ${kind}</h2><p class="field-error" data-form-error hidden></p>${fields.filter(([name]) => !immutable.includes(name)).map(([name, label, type, required, choices, bounds, hint]) => {
      const value = current?.[name] ?? "";
      const formatted = type === "datetime-local" ? toLocalDateTimeInput(value) : value;
      const control = type === "textarea"
        ? `<textarea id="faculty-${name}" name="${name}" ${required ? "required" : ""}>${escape(formatted)}</textarea>`
        : type === "select"
          ? `<select id="faculty-${name}" name="${name}" ${required ? "required" : ""}>${choices.map((choice) => {
              const optionValue = typeof choice === "string" ? choice : choice.value;
              const optionLabel = typeof choice === "string" ? choice : choice.label;
              return `<option value="${escape(optionValue)}" ${String(optionValue) === String(formatted) ? "selected" : ""}>${escape(optionLabel)}</option>`;
            }).join("")}</select>`
          : `<input id="faculty-${name}" name="${name}" type="${type}" value="${escape(formatted)}" ${required ? "required" : ""} ${type === "number" ? `step="any"${bounds ? ` min="${bounds.min}" max="${bounds.max}"` : ""}` : ""}/>`;
      const note = hint ? `<small>${escape(hint)}</small>` : "";
      return `<div class="form-group${type === "textarea" ? " wide" : ""}"><label for="faculty-${name}">${label}</label>${control}${note}</div>`;
    }).join("")}<div class="row-tight-lg"><button type="button" class="btn btn-outline" data-cancel>Cancel</button><button type="submit" class="btn btn-primary">${editing ? "Save" : "Create"}</button></div></form>`;
    document.body.append(dialog);
    dialog.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => dialog.remove());
    dialog.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!form.checkValidity()) return form.reportValidity();
      const payload = Object.fromEntries(new FormData(form));
      /* datetime-local values carry no zone: send the instant the user meant
         in THEIR time zone, not a naive string the server would read as UTC. */
      if (payload.due_at) payload.due_at = new Date(payload.due_at).toISOString();
      ["course_id", "student_id", "score", "max_score", "weightage_percent"].forEach((key) => {
        if (payload[key] === "") delete payload[key];
        else if (payload[key] !== undefined) payload[key] = Number(payload[key]);
      });
      if (kind === "mark" && payload.score > payload.max_score) {
        const node = form.querySelector("[data-form-error]");
        node.textContent = "Score cannot exceed the maximum score.";
        node.hidden = false;
        return;
      }
      try {
        form.querySelector("button[type=submit]").disabled = true;
        await onSave(payload);
        dialog.close();
      } catch (error) {
        const node = form.querySelector("[data-form-error]");
        node.textContent = error.message;
        node.hidden = false;
        form.querySelector("button[type=submit]").disabled = false;
      }
    });
    dialog.showModal();
  }

  async function showSubmissions(assignmentId, assignment) {
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog";
    dialog.innerHTML = '<form method="dialog"><h2>Submission grading</h2><p class="field-error" data-form-error hidden></p><div data-submissions>Loading submissions…</div><button type="button" class="btn btn-outline" data-cancel>Close</button></form>';
    document.body.append(dialog);
    dialog.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
    /* The assignment's max_score is the real ceiling: the snapshot stored on
       a submission is null until it is graded once. */
    const maxScore = assignment?.max_score ?? null;
    try {
      const submissions = await api.get(`/faculty/assignments/${assignmentId}/submissions`);
      const content = dialog.querySelector("[data-submissions]");
      content.innerHTML = submissions.length ? submissions.map((item) => `<section class="card"><p><strong>${escape(item.student_name)}</strong> (${escape(item.student_code)})</p><p>${escape(item.content)}</p><label>Score <input type="number" step="any" min="0" ${maxScore === null ? "" : `max="${maxScore}"`} value="${item.score ?? ""}" data-score="${item.id}" /></label><label>Feedback <textarea data-feedback="${item.id}">${escape(item.feedback)}</textarea></label><button type="button" class="btn btn-primary btn-sm" data-grade-id="${item.id}">Save grade</button></section>`).join("") : "No submissions are available.";
      content.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-grade-id]");
        if (!button) return;
        const id = button.dataset.gradeId;
        const score = Number(content.querySelector(`[data-score="${id}"]`).value);
        if (!Number.isFinite(score) || score < 0 || (maxScore !== null && score > maxScore)) {
          const node = dialog.querySelector("[data-form-error]");
          node.textContent = `Enter a score from 0 to ${maxScore ?? "the assignment maximum"}.`;
          node.hidden = false;
          return;
        }
        try {
          await api.post(`/faculty/submissions/${id}/grade`, { score, feedback: content.querySelector(`[data-feedback="${id}"]`).value || null, status: "graded" });
          button.textContent = "Saved";
          notify("Grade saved.");
        } catch (error) { const node = dialog.querySelector("[data-form-error]"); node.textContent = error.message; node.hidden = false; }
      });
    } catch (error) { dialog.querySelector("[data-submissions]").textContent = `Unable to load submissions: ${error.message}`; }
  }

  async function facultyPage() {
    const table = document.querySelector(".data-table");
    document.querySelectorAll(".user-name").forEach((node) => { node.textContent = "Faculty account"; });
    document.querySelectorAll(".user-role-label").forEach((node) => { node.textContent = "Authenticated faculty"; });
    const routes = { "assignments.html": "/faculty/assignments", "marks.html": "/faculty/marks" };
    if (!routes[page]) {
      if (table) setTableMessage(table, "This workflow requires faculty roster, timetable, or announcements data that the current API does not expose.");
      clearStatCards();
      document.querySelectorAll(".activity-list").forEach((list) => {
        list.innerHTML = '<div class="activity-item"><div class="activity-details"><span class="activity-title">Live data is unavailable for this view</span><span class="activity-meta">The current faculty API does not expose the data this screen needs.</span></div></div>';
      });
      const courseList = document.querySelector(".assigned-course-list");
      if (courseList) courseList.innerHTML = '<p class="empty-state">Your assigned courses are not exposed by the current faculty API.</p>';
      const banner = document.querySelector(".welcome-banner p");
      if (banner) banner.textContent = "This view needs faculty-scoped data that the current API does not expose.";
      setPageMessage("This screen is unavailable until the backend exposes the faculty-scoped data required to render it safely.", "info");
      return;
    }
    if (!table) return;
    setTableMessage(table, "Loading faculty data…");
    try {
      const data = await api.get(routes[page]);
      const body = table.querySelector("tbody");
      const primaryButton = document.querySelector(".app-topbar button.btn-primary");
      /* Submissions are the only faculty-scoped source of student identity
         and of the submission counts, so they are read once per assignment.
         A failure on one assignment degrades that row only. */
      const assignments = page === "assignments.html" ? data : await api.get("/faculty/assignments");
      const submissionLists = await Promise.all(assignments.map((assignment) =>
        api.get(`/faculty/assignments/${assignment.id}/submissions`).catch(() => null),
      ));
      const submissionsByAssignment = new Map(assignments.map((assignment, index) => [assignment.id, submissionLists[index]]));
      const students = new Map();
      submissionLists.forEach((list) => (list || []).forEach((submission) => students.set(submission.student_id, submission)));
      const courseIds = Array.from(new Set(assignments.map((assignment) => assignment.course_id))).sort((a, b) => a - b);
      const courseHint = courseIds.length
        ? `Course IDs the API returned for you: ${courseIds.join(", ")}.`
        : "The API has not returned a course for this account yet.";
      const studentHint = students.size
        ? `Students seen in your submissions: ${Array.from(students.values()).map((student) => `${student.student_id} (${student.student_name})`).join(", ")}.`
        : "No students are visible until they submit work for one of your assignments.";
      if (primaryButton) {
        primaryButton.disabled = false;
        primaryButton.addEventListener("click", () => showFacultyForm(page === "assignments.html" ? "assignment" : "mark", null, async (payload) => {
          await api.post(routes[page], payload);
          flash(page === "assignments.html" ? "Assignment created." : "Mark recorded.");
          location.reload();
        }, { courses: courseHint, students: studentHint }));
      }
      if (!data.length) {
        if (page === "assignments.html") {
          renderStatCards([{ label: "Active assignments", value: 0, meta: "No assignments are available" }, { label: "All assignments", value: 0, meta: "No led-course assignment data" }, { label: "Inactive assignments", value: 0, meta: "No records" }, { label: "Due dates", value: 0, meta: "No records" }]);
          const courseContext = document.querySelector(".course-context-strip");
          if (courseContext) courseContext.innerHTML = '<p class="empty-state">No assignments are available for this faculty account.</p>';
        } else {
          renderStatCards([{ label: "Recorded marks", value: 0, meta: "No marks are available" }, { label: "Average percentage", value: "—", meta: "No recorded marks" }, { label: "Highest percentage", value: "—", meta: "No recorded marks" }, { label: "Passing marks", value: "0 / 0", meta: "No recorded marks" }]);
          const selector = document.querySelector(".course-selector-bar");
          if (selector) selector.innerHTML = '<p class="empty-state">No marks are available. Use Create to record one for a student in a course you lead.</p>';
        }
        return setTableMessage(table, "No records are available. Use the Create action to add one.");
      }
      if (page === "assignments.html") {
        const active = data.filter((item) => item.is_active);
        const courseCount = courseIds.length;
        renderStatCards([
          { label: "Active assignments", value: active.length, meta: "Available to enrolled students" },
          { label: "All assignments", value: data.length, meta: `Across ${courseCount} led course${courseCount === 1 ? "" : "s"}` },
          { label: "Inactive assignments", value: data.length - active.length, meta: "Preserved for academic history" },
          { label: "Due dates", value: data.filter((item) => item.due_at).length, meta: "Set on listed assignments" },
        ]);
        const courseContext = document.querySelector(".course-context-strip");
        if (courseContext) courseContext.innerHTML = `<span class="course-code-chip">${courseCount}</span><div class="course-context-body fill"><span class="course-context-label">Assignment scope</span><span class="course-context-title">Assignments across led courses</span><span class="course-context-meta">Course names and roster data are not returned by this faculty endpoint.</span></div>`;
        /* The sidebar counter counts submissions still waiting for a grade. */
        const pendingReviews = Array.from(submissionsByAssignment.values()).reduce((sum, list) => sum + (list ? list.filter((entry) => entry.status !== "graded").length : 0), 0);
        document.querySelectorAll(".nav-counter").forEach((node) => { node.textContent = String(pendingReviews); });
        replaceTableHeaders(table, ["Course", "Assignment", "Due date", "Submissions", "Grading status", "Status", "Actions"]);
        body.innerHTML = data.map((item) => {
          const submissions = submissionsByAssignment.get(item.id);
          const graded = submissions ? submissions.filter((entry) => entry.status === "graded").length : null;
          const pending = submissions ? submissions.length - graded : null;
          const details = [item.description ? escape(item.description) : null, `Max ${item.max_score}`].filter(Boolean).join(" • ");
          return `<tr><td><strong>Course #${item.course_id}</strong></td><td><span class="cell-title">${escape(item.title)}</span><span class="cell-subtext">${details}</span></td><td>${formatDate(item.due_at)}</td><td>${submissions === null ? "—" : `${submissions.length} received`}</td><td>${graded === null ? "—" : `<span class="badge ${pending ? "badge-warning" : "badge-success"}">${graded} graded • ${pending} pending</span>`}</td><td><span class="badge ${item.is_active ? "badge-success" : "badge-warning"}">${item.is_active ? "Active" : "Inactive"}</span></td><td><button class="btn btn-outline btn-sm" data-assignment-id="${item.id}" data-faculty-action="submissions">View submissions</button> <button class="btn btn-text btn-sm" data-assignment-id="${item.id}" data-faculty-action="edit">Edit</button></td></tr>`;
        }).join("");
      } else {
        const scores = data.map((item) => item.percentage);
        const average = scores.length ? (scores.reduce((sum, value) => sum + value, 0) / scores.length).toFixed(1) : "—";
        const highest = scores.length ? Math.max(...scores).toFixed(1) : "—";
        const passing = scores.filter((value) => value >= 40).length;
        renderStatCards([
          { label: "Recorded marks", value: data.length, meta: "In courses you lead" },
          { label: "Average percentage", value: scores.length ? `${average}%` : "—", meta: "Across recorded marks" },
          { label: "Highest percentage", value: scores.length ? `${highest}%` : "—", meta: "Across recorded marks" },
          { label: "Passing marks", value: `${passing} / ${data.length}`, meta: "At least 40%" },
        ]);
        const selector = document.querySelector(".course-selector-bar");
        if (selector) selector.innerHTML = '<p class="empty-state">Use Create or Edit to manage individual marks. The current API does not expose a faculty course roster for a safe selector or spreadsheet export.</p>';
        replaceTableHeaders(table, ["Student", "Course", "Component", "Score", "Percentage", "Grade", "Actions"]);
        body.innerHTML = data.map((item) => {
          const student = students.get(item.student_id);
          const who = student
            ? `<span class="cell-title">${escape(student.student_name)}</span><span class="cell-subtext">${escape(student.student_code)}</span>`
            : `<strong>Student #${item.student_id}</strong>`;
          return `<tr><td>${who}</td><td>Course #${item.course_id}</td><td>${escape(item.component)}</td><td>${item.score} / ${item.max_score}</td><td>${item.percentage}%</td><td><span class="badge badge-primary">${escape(item.grade)}</span></td><td><button class="btn btn-text btn-sm" data-mark-id="${item.id}" data-faculty-action="edit">Edit</button></td></tr>`;
        }).join("");
      }
      table.addEventListener("click", (event) => {
        const button = event.target.closest("[data-faculty-action]");
        if (!button) return;
        if (button.dataset.facultyAction === "submissions") {
          const assignment = data.find((entry) => entry.id === Number(button.dataset.assignmentId));
          showSubmissions(button.dataset.assignmentId, assignment);
        }
        if (button.dataset.facultyAction === "edit") {
          const item = page === "assignments.html"
            ? data.find((entry) => entry.id === Number(button.dataset.assignmentId))
            : data.find((entry) => entry.id === Number(button.dataset.markId));
          showFacultyForm(page === "assignments.html" ? "assignment" : "mark", item, async (payload) => {
            await api.patch(`${routes[page]}/${item.id}`, payload);
            flash(page === "assignments.html" ? "Assignment updated." : "Mark updated.");
            location.reload();
          }, { courses: courseHint, students: studentHint });
        }
      });
    } catch (error) {
      setTableMessage(table, `Unable to load faculty data: ${error.message}`);
      setPageMessage(`Unable to load faculty data: ${error.message}`, "error");
      clearStatCards();
    }
  }

  /* ------------------------------------------------------------------
     CSV import dialog (students and faculty): upload -> review -> results.
     One dialog, one active step at a time. The two imports differ only in
     the columns, endpoints and wording collected in IMPORT_CONFIGS below;
     the server re-validates every row on confirm, so this dialog is a
     convenience, never the authority.
     ------------------------------------------------------------------ */

  const IMPORT_MAX_FILE_BYTES = 5 * 1024 * 1024;

  const IMPORT_CONFIGS = {
    students: {
      title: "Import Students",
      description: "Upload a CSV with one student per row, review the detected issues, then confirm to create accounts.",
      columns: ["email", "student_code", "full_name", "degree_program", "current_semester", "phone"],
      columnsNote: "Headers may be in any order; one student per row.",
      accountNoun: "students",
      previewPath: "/admin/students/import",
      confirmPath: "/admin/students/import/confirm",
      codeField: "student_code",
      codeLabel: "Student code",
      previewHeaders: ["#", "Email", "Student code", "Name", "Programme", "Semester", "Phone", "Status", "Errors"],
      previewCells: (row) => [
        row.row_number,
        escape(row.email),
        escape(row.student_code),
        escape(row.full_name),
        escape(row.degree_program),
        row.current_semester === null || row.current_semester === undefined ? "—" : `Sem ${escape(row.current_semester)}`,
        row.phone ? escape(row.phone) : "—",
      ],
      confirmRow: (row) => ({
        row_number: row.row_number,
        email: row.email,
        student_code: row.student_code,
        full_name: row.full_name,
        degree_program: row.degree_program || "",
        current_semester: row.current_semester ?? null,
        phone: row.phone || "",
      }),
      skippedText: "Skipped - email or roll number already exists",
      templateFile: "campus360-student-import-template.csv",
      templateExample: ["ada.lovelace@campus360.example", "ST-2026-0001", "Ada Lovelace", "B.Tech CSE", "3", ""],
      invalidRowsFile: "campus360-import-invalid-rows.csv",
      rejectedRowsFile: "campus360-import-rejected.csv",
    },
    faculty: {
      title: "Import Faculty",
      description: "Upload a CSV of faculty members, review the detected issues, then confirm to import.",
      columns: ["email", "faculty_code", "full_name", "department", "designation"],
      columnsNote: "Each row represents one faculty member; headers may be in any order.",
      accountNoun: "faculty",
      previewPath: "/admin/faculty/import",
      confirmPath: "/admin/faculty/import/confirm",
      codeField: "faculty_code",
      codeLabel: "Faculty code",
      previewHeaders: ["#", "Email", "Faculty code", "Name", "Department", "Designation", "Status", "Errors"],
      previewCells: (row) => [
        row.row_number,
        escape(row.email),
        escape(row.faculty_code),
        escape(row.full_name),
        escape(row.department),
        row.designation ? escape(row.designation) : "—",
      ],
      confirmRow: (row) => ({
        row_number: row.row_number,
        email: row.email,
        faculty_code: row.faculty_code,
        full_name: row.full_name,
        department: row.department || "",
        designation: row.designation || "",
      }),
      skippedText: "Skipped - email or faculty code already exists",
      templateFile: "campus360-faculty-import-template.csv",
      templateExample: ["ada.lovelace@campus360.example", "FAC-2026-0001", "Ada Lovelace", "Computer Science & Engineering", "Assistant Professor"],
      invalidRowsFile: "campus360-faculty-import-invalid-rows.csv",
      rejectedRowsFile: "campus360-faculty-import-rejected.csv",
    },
  };

  function showImportDialog(kind, onImported) {
    const config = IMPORT_CONFIGS[kind];
    const headerCells = (labels) => labels.map((label) => `<th scope="col">${escape(label)}</th>`).join("");
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog import-dialog";
    dialog.setAttribute("aria-labelledby", "import-title");
    dialog.setAttribute("aria-describedby", "import-desc");
    dialog.innerHTML = `
      <div class="import-modal">
        <header class="import-modal-header">
          <div>
            <h2 id="import-title">${escape(config.title)}</h2>
            <p id="import-desc" class="cell-subtext">${escape(config.description)}</p>
          </div>
        </header>

        <div class="import-steps-indicator" role="list" aria-label="Import progress">
          <div class="import-step-pill current" data-step-indicator="upload" aria-current="step">1. Upload</div>
          <svg class="import-step-connector" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" d="M1.5 6h9"/></svg>
          <div class="import-step-pill" data-step-indicator="preview">2. Review</div>
          <svg class="import-step-connector" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" d="M1.5 6h9"/></svg>
          <div class="import-step-pill" data-step-indicator="results">3. Results</div>
        </div>

        <p class="field-error" data-form-error role="alert" hidden></p>

        <div class="import-modal-body">
          <div class="import-step current" data-step="upload">
            <p class="cell-subtext">Required columns: <strong>${escape(config.columns.join(", "))}</strong>. ${escape(config.columnsNote)}</p>
            <p class="cell-subtext">No passwords are collected - ${escape(config.accountNoun)} choose their own at activation. Max 5 MB, up to 500 rows.</p>

            <div class="import-upload-card">
              <input type="file" accept=".csv" id="import-file" hidden />
              <label class="import-drop-zone" for="import-file" data-drop-zone tabindex="0" role="button" aria-label="Upload CSV file - drop it here or click to browse">
                <span class="import-drop-title">Drop CSV file here</span>
                <span class="import-drop-hint">or click to browse</span>
              </label>

              <div class="import-file-feedback" data-file-feedback hidden>
                <span class="import-file-icon" aria-hidden="true">&#128196;</span>
                <div>
                  <strong data-file-name></strong>
                  <span data-file-size></span>
                </div>
                <button type="button" class="btn btn-text btn-sm" data-file-remove>Remove file</button>
              </div>

              <div class="import-template-row">
                <button type="button" class="btn btn-outline btn-sm" data-download-template>Download template</button>
                <p class="cell-subtext" id="import-file-hint" aria-live="polite">Awaiting file…</p>
              </div>
            </div>
          </div>

          <div class="import-step" data-step="preview">
            <div class="import-summary" id="import-summary" hidden>
              <span class="import-summary-chip"><strong id="import-summary-total"></strong> total</span>
              <span class="import-summary-chip import-summary-chip-valid"><strong id="import-summary-valid"></strong> valid</span>
              <span class="import-summary-chip import-summary-chip-rejected"><strong id="import-summary-rejected"></strong> rejected</span>
            </div>
            <div class="import-table-wrap" id="import-preview-wrap" hidden>
              <table class="data-table import-preview-table" aria-label="Preview of validated rows">
                <thead><tr>${headerCells(config.previewHeaders)}</tr></thead>
                <tbody id="import-preview-rows"></tbody>
              </table>
            </div>
            <div class="import-table-actions" id="import-table-actions" hidden>
              <button type="button" class="btn btn-outline btn-sm" data-download-rejected>Download rejected rows (CSV)</button>
            </div>
          </div>

          <div class="import-step" data-step="results">
            <p class="cell-subtext" id="import-results-summary"></p>
            <p class="cell-subtext" id="import-email-note"></p>
            <div class="import-table-wrap" id="import-results-wrap" hidden>
              <table class="data-table" aria-label="Import outcome per row">
                <thead><tr>${headerCells(["#", "Email", config.codeLabel, "Name", "Status", "Outcome"])}</tr></thead>
                <tbody id="import-results-rows"></tbody>
              </table>
            </div>
            <div class="import-table-actions" id="import-results-actions" hidden>
              <button type="button" class="btn btn-outline btn-sm" data-download-rejected>Download rejected rows (CSV)</button>
            </div>
          </div>
        </div>

        <footer class="import-modal-footer">
          <div class="import-footer-actions upload-footer" hidden>
            <button type="button" class="btn btn-outline" data-cancel>Cancel</button>
            <button type="button" class="btn btn-primary" data-continue-import disabled>Continue</button>
          </div>
          <div class="import-footer-actions review-footer" hidden>
            <button type="button" class="btn btn-outline" data-back-to-upload>Back</button>
            <button type="button" class="btn btn-primary" data-confirm-import hidden>Confirm Import</button>
          </div>
          <div class="import-footer-actions results-footer">
            <button type="button" class="btn btn-primary" data-done-import>Done</button>
          </div>
        </footer>
      </div>
    `;
    document.body.append(dialog);

    const closeDialog = () => {
      dialog.close();
      dialog.remove();
    };
    dialog.addEventListener("close", () => dialog.remove());

    const $ = (selector) => dialog.querySelector(selector);
    const errorEl = $("[data-form-error]");
    const fileInput = $("#import-file");
    const dropZone = $("[data-drop-zone]");
    const fileFeedback = $("[data-file-feedback]");
    const fileHintEl = $("#import-file-hint");
    const continueBtn = $("[data-continue-import]");
    const backBtn = $("[data-back-to-upload]");
    const confirmBtn = $("[data-confirm-import]");
    const doneBtn = $("[data-done-import]");
    const cancelBtn = $("[data-cancel]");
    const steps = {
      upload: $("[data-step='upload']"),
      preview: $("[data-step='preview']"),
      results: $("[data-step='results']"),
    };
    const footers = {
      upload: $(".import-footer-actions.upload-footer"),
      preview: $(".import-footer-actions.review-footer"),
      results: $(".import-footer-actions.results-footer"),
    };
    const rowsEl = $("#import-preview-rows");
    const resultsRowsEl = $("#import-results-rows");
    const AWAITING_FILE = "Awaiting file…";

    let validRows = [];
    let previewRows = [];
    let confirming = false;
    let generation = 0;

    const formatBytes = (bytes) => (bytes >= 1024 * 1024
      ? `${(bytes / (1024 * 1024)).toFixed(2)} MB`
      : `${(bytes / 1024).toFixed(2)} KB`);

    const setError = (message) => {
      errorEl.textContent = message;
      errorEl.hidden = !message;
    };

    const showStep = (name) => {
      Object.entries(steps).forEach(([step, node]) => {
        node.hidden = step !== name;
        node.classList.toggle("current", step === name);
      });
      /* The footers carry display rules in CSS, so the inline style keeps the
         hidden ones out of the layout as well as out of the accessibility tree. */
      Object.entries(footers).forEach(([step, node]) => {
        node.hidden = step !== name;
        node.style.display = step === name ? "" : "none";
      });
      cancelBtn.hidden = name !== "upload";
      continueBtn.hidden = name !== "upload";
      backBtn.hidden = name !== "preview";
      confirmBtn.hidden = name !== "preview";
      confirmBtn.disabled = validRows.length === 0;
      doneBtn.hidden = name !== "results";
      dialog.querySelectorAll("[data-step-indicator]").forEach((pill) => {
        const active = pill.dataset.stepIndicator === name;
        pill.classList.toggle("current", active);
        if (active) pill.setAttribute("aria-current", "step");
        else pill.removeAttribute("aria-current");
      });
    };

    const resetFile = () => {
      generation += 1;
      fileInput.value = "";
      validRows = [];
      previewRows = [];
      continueBtn.disabled = true;
      fileFeedback.hidden = true;
      fileHintEl.textContent = AWAITING_FILE;
      setError("");
    };

    const rejectedCsv = (rows) => [["row_number", "email", config.codeField, "full_name", "status", "errors"]].concat(
      rows.map((row) => [row.row_number, row.email, row[config.codeField], row.full_name, row.status, (row.errors || []).join("; ")]),
    );

    const renderPreview = () => {
      const valid = previewRows.filter((row) => row.status === "valid").length;
      $("#import-summary-total").textContent = previewRows.length;
      $("#import-summary-valid").textContent = valid;
      $("#import-summary-rejected").textContent = previewRows.length - valid;
      $("#import-summary").hidden = false;
      rowsEl.innerHTML = previewRows.map((row) => `<tr>${config.previewCells(row).map((cell) => `<td>${cell}</td>`).join("")}<td><span class="badge ${row.status === "valid" ? "badge-success" : "badge-warning"}">${escape(row.status)}</span></td><td class="import-cell-errors">${escape((row.errors || []).join("; "))}</td></tr>`).join("");
      $("#import-preview-wrap").hidden = false;
      $("#import-table-actions").hidden = valid === previewRows.length;
    };

    $("#import-table-actions [data-download-rejected]").addEventListener("click", () => {
      downloadCsv(config.invalidRowsFile, rejectedCsv(previewRows.filter((row) => row.status !== "valid")));
    });

    const readFile = (file) => new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Could not read the file."));
      reader.readAsText(file);
    });

    const handleFile = async (file) => {
      resetFile();
      if (!file) return;
      const currentGen = generation;

      if (!file.name.toLowerCase().endsWith(".csv")) {
        setError("Please upload a .csv file.");
        return;
      }
      if (file.size > IMPORT_MAX_FILE_BYTES) {
        setError("File is too large (maximum 5 MB).");
        return;
      }

      fileHintEl.textContent = "Checking the file…";
      try {
        const text = await readFile(file);
        if (currentGen !== generation) return;
        const response = await api.post(config.previewPath, { csv_content: text });
        if (currentGen !== generation) return;

        $("[data-file-name]").textContent = file.name;
        $("[data-file-size]").textContent = `· ${formatBytes(file.size)}`;
        fileFeedback.hidden = false;

        /* A structural problem (missing columns, too many rows, bad encoding)
           comes back as file_error with no rows - it must be shown, not
           silently leave Continue disabled. */
        if (response.file_error) {
          fileHintEl.textContent = "The file could not be imported.";
          setError(response.file_error);
          return;
        }

        previewRows = response.rows || [];
        validRows = previewRows.filter((row) => row.status === "valid").map(config.confirmRow);
        if (!previewRows.length) {
          fileHintEl.textContent = "The file has no data rows.";
          setError("The CSV contains a header but no data rows.");
          return;
        }
        fileHintEl.textContent = validRows.length === previewRows.length
          ? `All ${previewRows.length} row${previewRows.length === 1 ? "" : "s"} passed validation. Continue to review.`
          : `${validRows.length} of ${previewRows.length} rows passed validation. Continue to review the problems.`;
        continueBtn.disabled = false;
      } catch (error) {
        if (currentGen === generation) {
          fileHintEl.textContent = AWAITING_FILE;
          setError(error.message || "Import preview failed.");
        }
      }
    };

    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.add("import-drag-over");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.remove("import-drag-over");
      });
    });
    dropZone.addEventListener("drop", (event) => {
      const files = event.dataTransfer?.files;
      if (files && files.length) handleFile(files[0]);
    });
    dropZone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        fileInput.click();
      }
    });
    fileInput.addEventListener("change", () => handleFile(fileInput.files[0]));
    $("[data-file-remove]").addEventListener("click", (event) => {
      event.preventDefault();
      resetFile();
    });

    $("[data-download-template]").addEventListener("click", (event) => {
      event.preventDefault();
      downloadCsv(config.templateFile, [config.columns, config.templateExample]);
    });

    continueBtn.addEventListener("click", () => {
      if (!previewRows.length) return;
      renderPreview();
      showStep("preview");
      setError(validRows.length ? "" : "No valid rows to import. Fix the errors shown below and upload the file again.");
    });

    backBtn.addEventListener("click", () => {
      showStep("upload");
      setError("");
    });

    cancelBtn.addEventListener("click", closeDialog);
    doneBtn.addEventListener("click", closeDialog);

    confirmBtn.addEventListener("click", async () => {
      if (confirming || validRows.length === 0) return;
      confirming = true;
      confirmBtn.disabled = true;
      backBtn.disabled = true;
      setError("");
      try {
        const response = await api.post(config.confirmPath, { rows: validRows });
        $("#import-results-summary").textContent = `Created: ${response.created} · Skipped: ${response.skipped} · Failed: ${response.failed}.`;
        $("#import-email-note").textContent = `Activation emails: ${response.email_sent} sent, ${response.email_failed} not sent.`;
        resultsRowsEl.innerHTML = response.rows.map((row) => {
          const outcome = row.status === "created"
            ? (row.email_sent ? "Account created - activation email queued" : "Account created - activation email not sent")
            : row.status === "skipped"
              ? config.skippedText
              : "Failed - could not create account";
          const badge = row.status === "created" ? "badge-success" : row.status === "skipped" ? "badge-secondary" : "badge-danger";
          return `<tr><td>${row.row_number}</td><td>${escape(row.email)}</td><td>${escape(row[config.codeField])}</td><td>${escape(row.full_name)}</td><td><span class="badge ${badge}">${escape(row.status)}</span></td><td class="import-cell-errors">${escape((row.errors || []).join("; ") || outcome)}</td></tr>`;
        }).join("");
        $("#import-results-wrap").hidden = !response.rows.length;
        const notCreated = response.rows.filter((row) => row.status !== "created");
        $("#import-results-actions").hidden = !notCreated.length;
        $("#import-results-actions [data-download-rejected]").onclick = () => downloadCsv(config.rejectedRowsFile, rejectedCsv(notCreated));
        showStep("results");
        if (onImported) Promise.resolve(onImported()).catch(() => {});
      } catch (error) {
        setError(error.message || "Import failed.");
        confirmBtn.disabled = false;
        backBtn.disabled = false;
        confirming = false;
      }
    });

    showStep("upload");
    dialog.showModal();
  }

  ready(() => {
    /* A save that reloaded the page left its confirmation behind. */
    consumeFlash();
    if (role !== "student") {
      api.get("/auth/me").then((user) => {
        document.querySelectorAll(".user-name").forEach((node) => { node.textContent = user.email; });
        document.querySelectorAll(".user-role-label").forEach((node) => { node.textContent = user.role; });
      }).catch(() => {});
    }
    if (role === "admin" && page === "dashboard.html") adminDashboard();
    else if (role === "admin" && page === "students.html") adminList("students");
    else if (role === "admin" && page === "faculty.html") adminList("faculty");
    else if (role === "admin" && page === "courses.html") adminList("courses");
    else if (role === "admin" && page === "notices.html") adminList("announcements");
    else if (role === "admin" && page === "reports.html") adminReports();
    else if (role === "student") studentPage();
    else if (role === "faculty") facultyPage();
  });
})();
