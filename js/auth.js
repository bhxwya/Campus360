/* ==========================================================================
   CAMPUS360 - DEMO AUTHENTICATION & ROLE ROUTING (auth.js)
   ==========================================================================
   Loaded on the login page and on every dashboard page.

   *** IMPORTANT - FRONTEND DEMO ONLY, NOT REAL AUTHENTICATION ***
   Everything in this file is a TEMPORARY stand-in for the login flow that
   the FastAPI backend will own. There is no credential checking of any kind:
   any non-empty ID and password is accepted.

   This is NOT secure and must not be treated as such. The stored state lives
   in the browser and is trivially editable by the visitor, so it only stops
   casual URL guessing in the prototype. Real security - hashed passwords,
   server-side sessions / JWTs, and authorization checks - has to be enforced
   by the backend, never by this file.

   What it does do:

     - validate that a role, an ID/email and a password were filled in
       (inline messages in the markup that already exists),
     - remember ONLY the selected role and a loggedIn flag,
     - NEVER read into a variable, store, or transmit the password,
     - route the visitor to the dashboard for the role they actually picked,
     - bounce dashboards back to the login page when no matching session
       exists.

   DEMO STATE (sessionStorage, key "campus360-demo-login")
     { "role": "student", "loggedIn": true }

   sessionStorage is used deliberately: the state dies with the tab, which is
   the correct lifetime for a throwaway demo login. The password is never
   part of it. A last-used role is kept separately in localStorage purely so
   the login page can pre-select the same pill on the next visit - that is a
   UI convenience, not part of the session.

   ROLE ROUTING
   Declared ONCE in the ROLE_ROUTES table below and never hardcoded anywhere
   else, so adding a role is a one-line change:

     student -> student/dashboard.html
     faculty -> faculty/dashboard.html
     admin   -> admin/dashboard.html

   Routes are relative to pages/login.html, which is where this logic lives.

   *** WHY <head> LOADING MATTERS HERE ***
   Both js files load from <head> where they are used (see the HTML
   comments), so everything needing markup runs through runWhenDomReady()
   below and the dashboard guard evaluates as soon as the document is
   parsed - before the visitor can interact with a protected page.
   ========================================================================== */

/* Storage keys. The session holds the login state; the role key is only a
   "remember my last choice" convenience for the login form. */
const DEMO_LOGIN_KEY = "campus360-demo-login";
const DEMO_ROLE_KEY = "campus360-demo-role";

/* --------------------------------------------------------------------------
   Role routing table - the single source of truth for role -> dashboard.
   The login form's radio values (student / faculty / admin) must match these
   keys EXACTLY after normalisation; normalizeRole() lowercases and trims
   first, so "Student" or " STUDENT " still resolve. An unknown value safely
   falls back to the student dashboard instead of navigating to a path that
   does not exist.
   -------------------------------------------------------------------------- */
const ROLE_ROUTES = {
  student: "student/dashboard.html",
  faculty: "faculty/dashboard.html",
  admin: "admin/dashboard.html",
};

/* Trim + lowercase so casing and stray spaces can never break a lookup. */
function normalizeRole(role) {
  return String(role || "").trim().toLowerCase();
}

function resolveRoleRoute(role) {
  return ROLE_ROUTES[normalizeRole(role)] || ROLE_ROUTES.student;
}

/* auth.js also loads from <head>: defer anything that queries the DOM until
   the document is parsed. (Mirrors onReady() in script.js, kept local so this
   file stays self-contained.) */
function runWhenDomReady(fn) {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fn, { once: true });
  } else {
    fn();
  }
}


/* ==========================================================================
   0. DEMO SESSION HELPERS (sessionStorage)
   --------------------------------------------------------------------------
   Every access is wrapped in try/catch because storage throws in private
   browsing and in contexts where site data is blocked. Failing to store the
   state is survivable: the next dashboard load simply redirects to login.
   ========================================================================== */

/* Read the demo session, or null when there is none / it is unusable. */
function readDemoLogin() {
  try {
    const raw = sessionStorage.getItem(DEMO_LOGIN_KEY);

    if (!raw) return null;

    const state = JSON.parse(raw);

    /* Only a complete { role, loggedIn:true } counts as a session. */
    return state && state.loggedIn && state.role ? state : null;
  } catch (err) {
    return null;
  }
}

/* Store the demo session: the selected role and the loggedIn flag ONLY.
   The password is never passed in here and never leaves the input element. */
function saveDemoLogin(role) {
  const state = JSON.stringify({ role: normalizeRole(role), loggedIn: true });

  try {
    sessionStorage.setItem(DEMO_LOGIN_KEY, state);
  } catch (err) {
    /* storage unavailable - the guard will redirect on the next page load */
  }
}

/* Remove the demo session (used by Sign Out). */
function clearDemoLogin() {
  try {
    sessionStorage.removeItem(DEMO_LOGIN_KEY);
  } catch (err) {
    /* ignore */
  }
}

/* The last role the visitor picked, so the login page can pre-select it.
   A UI preference only - deliberately NOT part of the session state. */
function readSavedRole() {
  try {
    return localStorage.getItem(DEMO_ROLE_KEY);
  } catch (err) {
    return null;
  }
}

function saveRole(role) {
  try {
    localStorage.setItem(DEMO_ROLE_KEY, normalizeRole(role));
  } catch (err) {
    /* ignore */
  }
}


/* ==========================================================================
   1. LOGIN PAGE - role detection, validation, routing
   ========================================================================== */

runWhenDomReady(() => {
  const loginForm = document.getElementById("login-form");

  /* Not the login page (this file also loads on every dashboard). */
  if (!loginForm) return;

  const idInput = document.getElementById("campus-id");
  const passwordInput = document.getElementById("password");
  const roleSelector = document.querySelector(".role-selector");

  /* NOTE ON ROLE LOOKUPS - the reason this used to be broken:
     the segmented role selector in login.html sits OUTSIDE
     <form id="login-form"> (it is a sibling <div> above the form), so every
     role-radio query MUST be scoped to `document`. Querying inside the form
     always returned null and every role fell back to the student dashboard. */
  const roleRadios = document.querySelectorAll('input[name="campus-role"]');

  /* The role actually selected right now, or "" when none is checked. */
  function getSelectedRole() {
    const checked = document.querySelector('input[name="campus-role"]:checked');
    return checked ? normalizeRole(checked.value) : "";
  }

  /* --- Inline validation -------------------------------------------------
     Each credential field already ships a sibling <p class="field-error">
     plus a .form-group wrapper. We write the message into it, flag the group
     with .has-error (auth.css paints the red border/icon for that), mark the
     input aria-invalid for screen readers, and clear it as soon as the user
     types. The role selector has no such message element, so it is reported
     with an inline note appended to .role-selector instead - still built from
     the same .field-error class so it matches the rest of the form. */
  function setFieldError(input, message) {
    const group = input.closest(".form-group");
    const error = document.getElementById(`${input.id}-error`);

    if (group) group.classList.add("has-error");
    input.setAttribute("aria-invalid", "true");

    if (error) {
      error.textContent = message;
      error.hidden = false;
    }
  }

  function clearFieldError(input) {
    const group = input.closest(".form-group");
    const error = document.getElementById(`${input.id}-error`);

    if (group) group.classList.remove("has-error");
    input.removeAttribute("aria-invalid");

    if (error) {
      error.textContent = "";
      error.hidden = true;
    }
  }

  /* The role message is created once, then reused, so repeated submits do not
     stack up duplicate nodes. */
  let roleError = null;

  function setRoleError(message) {
    if (!roleSelector) return;

    if (!roleError) {
      roleError = document.createElement("p");
      roleError.className = "field-error";
      roleError.setAttribute("role", "alert");
      roleSelector.appendChild(roleError);
    }

    roleError.textContent = message;
  }

  function clearRoleError() {
    if (roleError) roleError.textContent = "";
  }

  /* Clear a message while typing so the form self-corrects. */
  [idInput, passwordInput].forEach((input) => {
    if (input) input.addEventListener("input", () => clearFieldError(input));
  });

  /* Pre-select the role chosen on the last visit (markup defaults to
     Student). Scoped to `document` because the radios live outside the form. */
  const savedRole = normalizeRole(readSavedRole());

  if (savedRole) {
    const radio = document.querySelector(
      `input[name="campus-role"][value="${savedRole}"]`
    );
    if (radio) radio.checked = true;
  }

  /* Keep the remembered role in sync as the visitor switches pills. */
  roleRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      saveRole(radio.value);
      clearRoleError();
    });
  });

  /* --- Submit: DEMO validation + role-based routing ----------------------
     Real credential verification happens on the backend later; here any
     non-empty ID and password is accepted so the whole flow can be walked
     through. The password is read ONLY to test it is not empty - it is never
     stored, logged, or sent anywhere. */
  loginForm.addEventListener("submit", (event) => {
    /* Nothing leaves the page in this demo - always take over the submit. */
    event.preventDefault();

    let firstInvalid = null;

    /* 1. Role. The markup pre-checks Student, but the radio group is read
          live so the route always follows the pill the user actually chose. */
    const selectedRole = getSelectedRole();

    if (!selectedRole) {
      setRoleError("Please select Student, Faculty, or Admin.");
      firstInvalid = firstInvalid || (roleRadios[0] || roleSelector);
    } else {
      clearRoleError();
    }

    /* 2. Campus ID or institutional email. */
    if (!idInput.value.trim()) {
      setFieldError(
        idInput,
        "Please enter your Campus ID or institutional email."
      );
      firstInvalid = firstInvalid || idInput;
    } else {
      clearFieldError(idInput);
    }

    /* 3. Password - presence only, never stored. */
    if (!passwordInput.value) {
      setFieldError(passwordInput, "Please enter your password.");
      firstInvalid = firstInvalid || passwordInput;
    } else {
      clearFieldError(passwordInput);
    }

    /* Something is missing: keep the visitor here, show why, and move focus
       to the first problem so keyboard and screen-reader users land on it. */
    if (firstInvalid) {
      if (typeof firstInvalid.focus === "function") firstInvalid.focus();
      return;
    }

    /* All three present: remember the demo session (role + flag only) and
       route to the dashboard for the selected role. The FastAPI backend will
       issue a real session in place of this later. */
    saveRole(selectedRole);
    saveDemoLogin(selectedRole);

    window.location.href = resolveRoleRoute(selectedRole);
  });
});


/* ==========================================================================
   2. PROTECTED DASHBOARD PAGES - frontend demo guard
   --------------------------------------------------------------------------
   Every page under pages/<role>/ marks itself with <body data-demo-protected>.
   With no demo session, or a session belonging to a different role, the
   visitor is sent back to the login page.

   This is COSMETIC protection for the prototype only: the state is in the
   browser and can be edited by hand, so it stops casual URL guessing and
   nothing more. Real authorization must be enforced server-side.
   ========================================================================== */

runWhenDomReady(() => {
  const protectedBody = document.querySelector("body[data-demo-protected]");

  if (!protectedBody) return;

  /* The role this page belongs to comes from its own URL:
     .../pages/<role>/page.html. Read from the path rather than a data-*
     attribute because the markup is already written this way and it cannot
     drift out of sync with the folder. */
  const pathParts = window.location.pathname.split("/");
  const pageRole = normalizeRole(pathParts[pathParts.length - 2]);

  const session = readDemoLogin();

  const roleMismatch =
    !!session && !!pageRole && normalizeRole(session.role) !== pageRole;

  if (!session || roleMismatch) {
    /* replace(), not href: the protected page must not sit in the back-stack
       history, so Back cannot walk straight back into it. */
    window.location.replace("../login.html");
  }
});


/* ==========================================================================
   3. SIGN OUT
   --------------------------------------------------------------------------
   Every dashboard already has the control: <a class="logout-link"
   href="../login.html">Sign Out</a> in the sidebar footer. We intercept the
   click, clear the demo session, and navigate explicitly so the behaviour is
   identical no matter how the link is triggered (and so it also works if the
   href is ever changed by the backend team).
   ========================================================================== */

document.addEventListener("click", (event) => {
  const logoutLink = event.target.closest(".logout-link");

  if (!logoutLink) return;

  /* Take over: clear the session, then go to the login page. */
  event.preventDefault();
  clearDemoLogin();

  window.location.href = "../login.html";
});