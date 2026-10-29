/* ==========================================================================
   CAMPUS360 - ACCOUNT ACTIVATION (activate.js)
   ==========================================================================
   Public page: no session required. A single-use, time-bound token delivered
   by email proves the account holder's identity (imported students and
   faculty). They choose their own password here; the server verifies the token, hashes the password with
   Argon2id, and activates the account.

   Security properties:
   * The token is never stored in localStorage or sessionStorage.
   * The token is never logged by this file.
   * No analytics or external resources are loaded on this page.
   * Password match is checked client-side for fast feedback, but the server
     is the authority and rejects mismatches/weak passwords.
   ========================================================================== */

(function () {
  const form = document.getElementById("activate-form");
  const submitBtn = document.getElementById("activate-submit");
  const cancelBtn = document.getElementById("activate-cancel");
  const tokenInput = document.getElementById("activate-token");
  const passwordInput = document.getElementById("activate-password");
  const confirmInput = document.getElementById("activate-password-confirm");
  const generalError = document.getElementById("activate-general-error");
  const passwordError = document.getElementById("activate-password-error");
  const confirmError = document.getElementById("activate-confirm-error");
  const successBox = document.getElementById("activate-success");

  /* Read the single-use token from the ?token= query parameter. It lives only
     in this page's memory - never written to storage. */
  const token = new URLSearchParams(window.location.search).get("token");
  if (!token) {
    generalError.textContent = "No activation token in the link. Please use the link from your email.";
    generalError.hidden = false;
    form.hidden = true;
    submitBtn.disabled = true;
    return;
  }
  tokenInput.value = token;

  /* Cancel returns to the sign-in page (a sibling of this page in pages/). */
  cancelBtn.addEventListener("click", () => {
    window.location.href = "login.html";
  });

  /* Clear field errors as the user types. */
  [passwordInput, confirmInput].forEach((input) => {
    input.addEventListener("input", () => {
      passwordError.hidden = true;
      confirmError.hidden = true;
      generalError.hidden = true;
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    generalError.hidden = true;
    passwordError.hidden = true;
    confirmError.hidden = true;

    const password = passwordInput.value;
    const confirm = confirmInput.value;

    if (password !== confirm) {
      confirmError.textContent = "The two passwords do not match.";
      confirmError.hidden = false;
      confirmInput.focus();
      return;
    }

    /* Minimal client-side length check; the server enforces the full policy. */
    if (password.length < 8 || password.length > 128) {
      passwordError.textContent = "Password must be between 8 and 128 characters.";
      passwordError.hidden = false;
      passwordInput.focus();
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Setting password…";

    try {
      /* Raw fetch: activation is unauthenticated, so no CSRF cookie exists
         and api.js would reject the request. The token itself (unguessable,
         single-use, time-bound) is the authorization. */
      const response = await fetch("/api/v1/auth/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });

      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        /* Generic message either way - never reveal whether an email/token
           exists (anti-enumeration). */
        const detail = payload && payload.detail;
        generalError.textContent = typeof detail === "string" && detail
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => (item && item.msg) || "").filter(Boolean).join(" ")
            : "Unable to activate. The link may be expired or already used.";
        generalError.hidden = false;
        submitBtn.disabled = false;
        submitBtn.textContent = "Set password & activate";
        return;
      }

      form.hidden = true;
      successBox.hidden = false;
    } catch (err) {
      generalError.textContent = "Unable to reach the server. Check your connection and try again.";
      generalError.hidden = false;
      submitBtn.disabled = false;
      submitBtn.textContent = "Set password & activate";
    }
  });
})();
