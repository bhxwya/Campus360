/* ==========================================================================
   CAMPUS360 - AUTHENTICATION & ROLE ROUTING (auth.js)
   ==========================================================================
   Loaded on the login page and on every dashboard page.

   *** BACKEND-INTEGRATED ***
   Credentials are sent to FastAPI over the current origin. The server owns
   the HttpOnly session cookie and remains the authority for every role check.

   What it does do:

     - validate that a role, an email and a password were filled in
       (inline messages in the markup that already exists),
     - remember only the selected role as a UI preference,
     - submit credentials once to the backend,
     - compare the selected role pill with the role the SERVER reports and
       refuse to route on a mismatch (revoking the session the server just
       opened for the other role - the pill never grants anything),
     - route the visitor according to the authenticated server response,
     - bounce dashboards back to the login page when no matching session exists

   The password is never stored in browser storage. A last-used role is kept
   separately only to pre-select a login-form pill on a later visit.

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

/* The backend now manages session state via HttpOnly cookies.
   The frontend should not store authentication proof in browser storage.
   The campus360_csrf cookie (readable) is used for CSRF protection.
   The campus360_session cookie (HttpOnly) is set by the backend
   and automatically sent by the browser with credentials: "include".
   The DEMO_ROLE_KEY below is a UI convenience only - it pre-selects the
   role pill on the login form on subsequent visits and stores no auth proof. */
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

/* Feedback for sign-out and other shell-wide failures. Prefers the shared
   corner toast defined by js/script.js; falls back to a plain alert only if
   that file is somehow absent. */
function notifyUser(message, kind) {
  if (typeof window.Campus360Notify === "function") {
    window.Campus360Notify(message, kind);
  } else {
    window.alert(message);
  }
}

/* Human label for a role, used only in messages such as
   "This account is registered as a Student." Unknown values stay "Unknown"
   rather than being folded into a real role name. */
function roleLabel(role) {
  const normalised = normalizeRole(role);
  return normalised ? normalised.charAt(0).toUpperCase() + normalised.slice(1) : "Unknown";
}

/* The noun phrase for "registered as …" so the sentence reads naturally for
   every role: a Student / a Faculty member / an Admin. */
function roleArticle(role) {
  const label = roleLabel(role);
  if (normalizeRole(role) === "faculty") return `a ${label} member`;
  return `${/^[aeiou]/i.test(label) ? "an" : "a"} ${label}`;
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

/* The sign-in page, relative to any page under pages/<role>/, with an
   optional reason the login form explains (see showSignInNotice). */
function loginUrl(reason) {
  return reason ? `../login.html?reason=${encodeURIComponent(reason)}` : "../login.html";
}


/* ==========================================================================
   0. COOKIE AND PREFERENCE HELPERS
   --------------------------------------------------------------------------
   Storage access is wrapped in try/catch because it throws in private
   browsing and where site data is blocked; the role preference is optional.
   ========================================================================== */

/* Read the readable campus360_csrf cookie for use in X-CSRF-Token headers.
   The cookie is set by the backend after login and is NOT HttpOnly, so
   JavaScript can read it via document.cookie. */
function readCsrfCookie() {
  const cookies = document.cookie.split(";");
  for (const cookie of cookies) {
    const [name, value] = cookie.trim().split("=");
    if (name === "campus360_csrf") return decodeURIComponent(value || "");
  }
  return null;
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

  /* --- Sign-in notices ---------------------------------------------------
     The API client and the dashboard guard send visitors here with a reason
     when a session ends or access is refused, instead of leaving a protected
     page showing a generic load error. The notice is a live region, so it is
     announced as soon as it is filled. */
  (function showSignInNotice() {
    const notice = document.getElementById("auth-notice");
    if (!notice) return;
    const messages = {
      expired: "Your session expired. Please sign in again to continue.",
      forbidden: "You do not have access to that page. Sign in with an account that has permission.",
    };
    const reason = new URLSearchParams(window.location.search).get("reason");
    if (!reason || !messages[reason]) return;
    notice.textContent = messages[reason];
    notice.hidden = false;
  })();

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
      /* A "this account is registered as …" message describes the previous
         attempt, so it must not survive a role change. */
      const loginError = document.getElementById("login-error");
      if (loginError) loginError.hidden = true;
    });
  });

  /* A role mismatch means the credentials were valid but belong to a different
     account type. The server has already opened a session by then, so that
     session is revoked again through the normal, CSRF-protected logout
     endpoint: the browser must not be left signed in as the other role just
     because the visitor picked the wrong pill. */
  async function revokeLoginSession() {
    const csrfToken = readCsrfCookie();
    if (!csrfToken) return;
    try {
      await fetch("/api/v1/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });
    } catch (error) {
      /* Staying on the login form is safe even if revocation fails: every
         dashboard re-checks the role against /auth/me server-side, so a
         leftover cookie can never grant access to a mismatched page. */
    }
  }

  let loginInProgress = false;

  /* Validate locally, then submit exactly one request to the backend. */
  loginForm.addEventListener("submit", async (event) => {
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

    /* 2. Institutional email - the API signs in by email only. */
    if (!idInput.value.trim()) {
      setFieldError(idInput, "Please enter your institutional email.");
      firstInvalid = firstInvalid || idInput;
    } else if (!/^[^\s@]+@[^\s@]+$/.test(idInput.value.trim())) {
      setFieldError(idInput, "Enter your institutional email address, for example user@campus.edu.");
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

    if (loginInProgress) return;
    loginInProgress = true;
    const submitBtn = loginForm.querySelector("button.auth-submit-btn");
    const errorEl = document.getElementById("login-error");
    if (errorEl) errorEl.hidden = true;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Signing in…";
    }

    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: idInput.value.trim(), password: passwordInput.value }),
        credentials: "include",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        /* FastAPI uses a string for auth failures but an array of field
           errors for validation failures. Normalise both before it reaches
           textContent, which would otherwise print "[object Object]". */
        const detail = payload && payload.detail;
        const message = typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => (item && item.msg) || "").filter(Boolean).join(" ")
            : (detail && (detail.message || detail.msg)) || "";
        throw new Error(message || "Unable to sign in. Please try again.");
      }

      /* The server decides the role - the selected pill only states what the
         visitor expected. An unrecognised role is refused too, because
         resolveRoleRoute() would otherwise silently fall back to the student
         dashboard. */
      const authenticatedRole = normalizeRole(payload.role);
      if (!ROLE_ROUTES[authenticatedRole]) {
        await revokeLoginSession();
        throw new Error("The server did not return a recognised account role. Please try again.");
      }

      /* Mismatch: no dashboard navigation, no "success" state. Explain the
         real role and leave the visitor on this form. */
      if (authenticatedRole !== selectedRole) {
        await revokeLoginSession();
        if (errorEl) {
          errorEl.textContent = `This account is registered as ${roleArticle(authenticatedRole)}. Please select ${roleLabel(authenticatedRole)} login to continue.`;
          errorEl.hidden = false;
        }
        const checkedRole = document.querySelector('input[name="campus-role"]:checked');
        if (checkedRole) checkedRole.focus();
        return;
      }

      saveRole(authenticatedRole);
      window.location.assign(resolveRoleRoute(authenticatedRole));
    } catch (error) {
      if (errorEl) {
        errorEl.textContent = error.message || "Unable to sign in. Please try again.";
        errorEl.hidden = false;
      }
    } finally {
      loginInProgress = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Sign In to Dashboard";
      }
    }
  });
});


/* ==========================================================================
   2. PROTECTED DASHBOARD PAGES - session guard
   --------------------------------------------------------------------------
   Every page under pages/<role>/ marks itself with <body data-demo-protected>.
   The page asks the server who is signed in (GET /auth/me). No session, or a
   session for a different role, sends the visitor to the login page with a
   reason. This only decides what to SHOW: every API route enforces the role
   server-side, so the data stays protected even if this script is bypassed.
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

  /* Verify the server session and role match the page. */
  fetch("/api/v1/auth/me", { credentials: "include" })
    .then((res) => {
      if (!res.ok) throw new Error("Not authenticated");
      return res.json();
    })
    .then((user) => {
      if (normalizeRole(user.role) !== pageRole) {
        window.location.replace(loginUrl("forbidden"));
      }
    })
    .catch(() => {
      window.location.replace(loginUrl("expired"));
    });
});


/* ==========================================================================
   3. SIGN OUT
   --------------------------------------------------------------------------
   Every dashboard already has the control: <a class="logout-link"
   href="../login.html">Sign Out</a> in the sidebar footer. We intercept the
   click, revoke the server session, and then navigate to the login page.
   A session that is already gone (expired, revoked, or missing its CSRF
   cookie) needs no revocation, so the visitor is simply taken to sign in.
   ========================================================================== */

document.addEventListener("click", async (event) => {
  const logoutLink = event.target.closest(".logout-link");

  if (!logoutLink) return;

  /* Take over: send logout request to the backend, then go to login page. */
  event.preventDefault();
  const csrfToken = readCsrfCookie();
  if (!csrfToken) {
    window.location.href = loginUrl();
    return;
  }
  try {
    const resp = await fetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      credentials: "include"
    });
    if (resp.status === 204 || resp.status === 401) {
      window.location.href = loginUrl();
    } else {
      notifyUser("Sign out failed. Please try again.", "error");
    }
  } catch (error) {
    notifyUser("Sign out could not reach the server. Check your connection and try again.", "error");
  }
});
