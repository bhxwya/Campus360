/* Campus360 shared same-origin API client. Authentication stays in HttpOnly
   cookies; unsafe requests include the server-issued CSRF token. */
(function () {
  function csrfToken() {
    return document.cookie.split(";").reduce((token, entry) => {
      const [name, value] = entry.trim().split("=");
      return name === "campus360_csrf" ? decodeURIComponent(value || "") : token;
    }, "");
  }

  /* One signed-out redirect per page load. An expired or revoked session must
     not leave stale data on screen, and a burst of parallel requests must not
     fire several navigations. */
  let redirecting = false;
  function redirectToLogin(reason) {
    if (redirecting) return;
    if (/\/pages\/login\.html$/.test(window.location.pathname)) return;
    redirecting = true;
    window.location.replace(`/pages/login.html?reason=${encodeURIComponent(reason)}`);
  }

  function validationFieldErrors(detail) {
    const fieldErrors = {};
    detail.forEach((item) => {
      if (!Array.isArray(item?.loc)) return;
      const field = item.loc.filter((part) => part !== "body").pop();
      if (typeof field === "string" && field && !fieldErrors[field]) {
        fieldErrors[field] = item.msg;
      }
    });
    return fieldErrors;
  }

  /* Keep both the human message and any field map the API supplies. Duplicate
     student email/roll errors arrive as {message, field_errors}; FastAPI's
     validation errors arrive as an array with loc entries. */
  function errorFromPayload(payload, status) {
    const detail = payload?.detail;
    if (Array.isArray(detail)) {
      const message = detail.map((item) => {
        const field = Array.isArray(item?.loc)
          ? item.loc.filter((part) => part !== "body").join(".")
          : "";
        return field ? `${field}: ${item.msg}` : item.msg;
      }).join(" ");
      return {
        message: message || `Request failed (${status}).`,
        fieldErrors: validationFieldErrors(detail),
      };
    }
    if (detail && typeof detail === "object") {
      const source = detail.field_errors || detail.errors;
      const fieldErrors = {};
      if (source && typeof source === "object" && !Array.isArray(source)) {
        Object.entries(source).forEach(([field, message]) => {
          if (typeof message === "string" && message) fieldErrors[field] = message;
        });
      }
      return {
        message: typeof detail.message === "string" && detail.message
          ? detail.message
          : `Request failed (${status}).`,
        fieldErrors,
      };
    }
    return {
      message: typeof detail === "string" && detail
        ? detail
        : `Request failed (${status}).`,
      fieldErrors: {},
    };
  }

  async function request(path, options) {
    const settings = options || {};
    const headers = new Headers(settings.headers || {});
    const method = (settings.method || "GET").toUpperCase();
    if (settings.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const token = csrfToken();
      /* Fail fast and loudly: an unsafe request without the synchronizer
         token would be rejected by the server anyway, and a clear message
         beats an opaque 403. */
      if (!token) {
        const error = new Error("Security token missing. Reload the page and sign in again.");
        error.status = 403;
        error.fieldErrors = {};
        throw error;
      }
      headers.set("X-CSRF-Token", token);
    }

    const response = await fetch(`/api/v1${path}`, {
      ...settings,
      method,
      headers,
      credentials: "include",
      body: settings.body === undefined ? undefined : JSON.stringify(settings.body),
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : null;
    if (!response.ok) {
      const parsed = errorFromPayload(payload, response.status);
      const error = new Error(parsed.message);
      error.status = response.status;
      error.fieldErrors = parsed.fieldErrors;
      error.detail = payload?.detail;
      /* 401 means the session is gone: explain it on the sign-in page instead
         of letting each page show a generic load failure. 403 (wrong role) is
         a per-page message, not a sign-out. */
      if (response.status === 401) redirectToLogin("expired");
      throw error;
    }
    return payload;
  }

  window.Campus360Api = {
    get: (path) => request(path),
    post: (path, body) => request(path, { method: "POST", body }),
    patch: (path, body) => request(path, { method: "PATCH", body }),
    delete: (path) => request(path, { method: "DELETE" }),
  };
})();
