/* ==========================================================================
   CAMPUS360 - SHARED SITE BEHAVIOUR (script.js)
   ==========================================================================
   Loaded from the <head> of EVERY page (landing, login, and all dashboards).
   Campus360 is LIGHT-MODE ONLY - there is no theme logic in this file.
   (js/auth.js joins it on the login and dashboard pages only.)

   Behaviour shared by the whole frontend:

      1. Navbar scroll state        -> landing page header compaction
      2. Same-page anchor scrolling  -> landing page in-page navigation
      3. Application shell polish    -> skip link, aria-current, notifier

   Authentication / role routing lives in js/auth.js. Data binding lives in
   js/portal.js, which owns the server-side table search and every page's
   real content (so this file does NOT filter table rows a second time).

   EVERYTHING in this file is safe to load on any page: each behaviour first
   looks for its own elements and returns silently when they are absent, so
   loading this on a login or dashboard page (which has none of them) is a
   clean no-op rather than an error.
   ========================================================================== */


/* ==========================================================================
   0. SHARED HELPERS
   --------------------------------------------------------------------------
   Both files load from <head>, so the document is still parsing when this
   runs. Anything that touches page markup has to wait for the parser to
   finish - that is what onReady() is for.
   ========================================================================== */

/* Run fn once the DOM exists: immediately when the document has already been
   parsed, otherwise on DOMContentLoaded (the normal case for a <head> script). */
function onReady(fn) {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fn, { once: true });
  } else {
    fn();
  }
}

/* True when the visitor has asked the OS to reduce motion. Respected by the
   anchor scrolling below so smooth-scroll never fights accessibility needs. */
function prefersReducedMotion() {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/* Sidebar preference. The value is read in the <head> (below) so the desktop
   rail is already at its saved width on first paint, rather than expanding
   after the DOM is ready and shifting the page. */
const CAMPUS360_SIDEBAR_STORAGE_KEY = "campus360.sidebarCollapsed";

(function restoreSidebarPreference() {
  try {
    if (window.localStorage.getItem(CAMPUS360_SIDEBAR_STORAGE_KEY) === "true") {
      document.documentElement.classList.add("sidebar-collapsed");
    }
  } catch (error) {
    /* Storage can be unavailable (private mode / blocked cookies). The
       expanded rail is a safe default and everything still works. */
  }
})();


/* ==========================================================================
   1. NAVBAR SCROLL STATE
   --------------------------------------------------------------------------
   The landing page header compacts and fades in its frosted background once
   the user scrolls past the top. Only the landing page has a .navbar, so on
   every other page this finds nothing and returns - a deliberate no-op.
   ========================================================================== */

onReady(() => {
  const navbar = document.querySelector(".navbar");

  if (!navbar) return;

  const updateNavbarState = () => {
    navbar.classList.toggle("scrolled", window.scrollY > 50);
  };

  /* Run once immediately: the page may already be scrolled (deep link, reload
     mid-page, back navigation), and the header must match on first paint. */
  updateNavbarState();

  /* passive: true - this listener never calls preventDefault, so the browser
     is free to keep scrolling on the compositor thread. */
  window.addEventListener("scroll", updateNavbarState, { passive: true });
});


/* ==========================================================================
   2. SAME-PAGE ANCHOR SCROLLING
   --------------------------------------------------------------------------
   The landing page navigates with in-page links (#features, #about,
   #contact, #platform-preview) and the navbar sits fixed over the content.

   The stylesheet already sets `scroll-behavior: smooth` plus
   `scroll-padding-top: 88px` on <html>, so the browser's own anchor jump
   handles the easing AND clears the fixed navbar. This handler therefore
   stays deliberately minimal - it only:

     - ignores anything that is not a same-page "#..." link,
     - ignores clicks that are modified (new tab / new window / download),
     - skips the animation when the visitor prefers reduced motion.

   Doing nothing is the correct result on every other page: the delegated
   listener is on `document`, but the #check below means it never acts.
   ========================================================================== */

onReady(() => {
  document.addEventListener("click", (event) => {
    /* Ignore modified clicks so Ctrl/Cmd/middle-click still open a new tab
       exactly as the browser intends. */
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    const link = event.target.closest('a[href^="#"]');

    if (!link) return;

    const hash = link.getAttribute("href");

    /* "#" alone and an empty href are not section links - leave them alone
       (e.g. the login page's "Forgot password?" placeholder). */
    if (!hash || hash === "#" || hash.length < 2) return;

    const target = document.getElementById(hash.slice(1));

    /* No such section on this page: fall back to the browser's default
       behaviour rather than silently swallowing the click. */
    if (!target) return;

    event.preventDefault();

    /* Reduced motion: jump straight to the section, no easing. */
    target.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "start",
    });

    /* Keep the URL shareable/bookmarkable, and make the back button behave.
       pushState (not location.hash) so we do not add a history entry the
       browser would not have added. */
    window.history.pushState(null, "", hash);
  });
});


/* ==========================================================================
   3. APPLICATION SHELL POLISH
   --------------------------------------------------------------------------
   The dashboard shell is repeated in every page's markup, so the details that
   make it accessible are applied here once rather than edited into eighteen
   files:

     - a "Skip to main content" link that appears on keyboard focus,
     - id + tabindex on <main>, so the skip link can move focus there,
     - aria-current="page" on the active sidebar link (the visual .active
       class already exists; screen readers need the semantic equivalent),
     - a label on the primary <nav> and the sidebar landmark.

   Each step checks for its element first, so the landing and login pages
   (which have no app shell) are untouched.
   ========================================================================== */

onReady(() => {
  const main = document.querySelector("main.app-main");
  const layout = document.querySelector(".app-layout");

  if (main && layout) {
    if (!main.id) main.id = "main-content";
    /* tabindex="-1" lets the skip link focus the region without adding it to
       the tab order. */
    if (!main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");

    if (!document.querySelector(".skip-link")) {
      const skipLink = document.createElement("a");
      skipLink.className = "skip-link";
      skipLink.href = "#main-content";
      skipLink.textContent = "Skip to main content";
      /* Fragment navigation scrolls to <main>; focusing it as well moves the
         keyboard cursor, which some browsers do not do for the target of a
         fragment link. preventScroll keeps the browser's own scroll. */
      skipLink.addEventListener("click", () => {
        main.focus({ preventScroll: true });
      });
      document.body.insertBefore(skipLink, document.body.firstChild);
    }
  }

  const sidebar = document.querySelector(".app-sidebar");
  if (sidebar && !sidebar.hasAttribute("aria-label")) {
    sidebar.setAttribute("aria-label", "Campus360 workspace");
  }

  const nav = document.querySelector(".sidebar-nav");
  if (nav && !nav.hasAttribute("aria-label")) {
    nav.setAttribute("aria-label", "Primary navigation");
  }

  document.querySelectorAll(".nav-link.active").forEach((link) => {
    link.setAttribute("aria-current", "page");
  });
});


/* ==========================================================================
   4. COLLAPSIBLE SIDEBAR + MOBILE DRAWER
   --------------------------------------------------------------------------
   All three portals render the same .app-layout / .app-sidebar shell, so the
   controls and state live here once rather than in eighteen page files.

     Desktop (> 768px)
       - the topbar toggle collapses the rail to an icon strip
       - the preference is stored in localStorage and re-applied in <head>
       - the main column stays in normal flow, so it reflows instead of being
         covered by the rail

     Mobile (<= 768px)
       - the same toggle opens a drawer; a close button sits inside it
       - an overlay blocks the obscured page, and the main region is inert
       - Escape, the overlay, the close button and following a link all close
         it; focus returns to the toggle unless the visitor is navigating

   Every lookup is guarded, so pages without an application shell are still a
   clean no-op.
   ========================================================================== */

onReady(() => {
  const layout = document.querySelector(".app-layout");
  const sidebar = document.querySelector(".app-sidebar");
  const main = document.querySelector("main.app-main");
  const topbar = main && main.querySelector(".app-topbar");

  if (!layout || !sidebar || !main || !topbar) return;

  const root = document.documentElement;
  const mobileQuery = window.matchMedia("(max-width: 768px)");
  let drawerOpen = false;
  let focusBeforeDrawer = null;

  if (!sidebar.id) sidebar.id = "app-sidebar";

  /* --- Controls --------------------------------------------------------- */

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "sidebar-toggle";
  toggle.setAttribute("aria-controls", sidebar.id);
  toggle.dataset.sidebarToggle = "";
  toggle.innerHTML =
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/><path d="m15.5 9-3 3 3 3"/></svg>';

  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "sidebar-drawer-close";
  closeButton.setAttribute("aria-label", "Close navigation menu");
  closeButton.innerHTML =
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';

  const overlay = document.createElement("div");
  overlay.className = "sidebar-overlay";
  overlay.setAttribute("aria-hidden", "true");

  topbar.insertBefore(toggle, topbar.firstElementChild);
  sidebar.insertBefore(closeButton, sidebar.firstChild);
  document.body.append(overlay);

  /* --- Labels ----------------------------------------------------------- */

  /* CSS visually removes these labels in the collapsed rail. Copying them to
     title/aria-label keeps every icon identifiable by pointer and screen
     reader while the text is no longer visible. */
  sidebar.querySelectorAll(".nav-link").forEach((link) => {
    const label = Array.from(link.children).find(
      (child) => child.matches("span:not(.nav-icon):not(.nav-counter)")
    );
    const name = label ? label.textContent.trim() : "";
    if (!name) return;
    link.dataset.sidebarLabel = name;
    link.setAttribute("title", name);
    link.setAttribute("aria-label", name);
  });

  const profileLink = sidebar.querySelector(".user-profile-link");
  if (profileLink) {
    profileLink.setAttribute("title", "View profile");
    profileLink.setAttribute("aria-label", "View profile");
  }

  const logoutLink = sidebar.querySelector(".logout-link");
  if (logoutLink) {
    logoutLink.setAttribute("title", "Sign Out");
    logoutLink.setAttribute("aria-label", "Sign Out");
  }

  /* --- State ------------------------------------------------------------ */

  const isCollapsed = () => root.classList.contains("sidebar-collapsed");

  const setInert = (element, value) => {
    if ("inert" in element) {
      element.inert = value;
    } else if (value) {
      element.setAttribute("aria-hidden", "true");
    } else {
      element.removeAttribute("aria-hidden");
    }
  };

  const updateToggle = () => {
    const mobile = mobileQuery.matches;
    const expanded = mobile ? drawerOpen : !isCollapsed();
    const label = mobile
      ? drawerOpen
        ? "Close navigation menu"
        : "Open navigation menu"
      : isCollapsed()
        ? "Expand sidebar"
        : "Collapse sidebar";

    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("title", label);
  };

  const persistCollapsed = (collapsed) => {
    try {
      window.localStorage.setItem(CAMPUS360_SIDEBAR_STORAGE_KEY, String(collapsed));
    } catch (error) {
      /* A failed write must not stop the in-page toggle from working. */
    }
  };

  const setCollapsed = (collapsed) => {
    root.classList.toggle("sidebar-collapsed", collapsed);
    persistCollapsed(collapsed);
    updateToggle();
  };

  const openDrawer = () => {
    if (!mobileQuery.matches || drawerOpen) return;

    focusBeforeDrawer =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    drawerOpen = true;
    root.classList.add("sidebar-drawer-open");
    setInert(sidebar, false);
    setInert(main, true);
    updateToggle();

    /* Move the keyboard cursor into the drawer after the open state has been
       applied, so the close button is visible before it receives focus. */
    window.requestAnimationFrame(() => closeButton.focus());
  };

  const closeDrawer = ({ restoreFocus = true } = {}) => {
    if (!drawerOpen) return;

    drawerOpen = false;
    root.classList.remove("sidebar-drawer-open");
    setInert(sidebar, true);
    setInert(main, false);
    updateToggle();

    if (restoreFocus) {
      const target = focusBeforeDrawer && document.contains(focusBeforeDrawer)
        ? focusBeforeDrawer
        : toggle;
      target.focus();
    }
    focusBeforeDrawer = null;
  };

  const applyViewportState = () => {
    if (mobileQuery.matches) {
      drawerOpen = false;
      root.classList.remove("sidebar-drawer-open");
      if (sidebar.contains(document.activeElement)) toggle.focus();
      setInert(sidebar, true);
      setInert(main, false);
    } else {
      drawerOpen = false;
      root.classList.remove("sidebar-drawer-open");
      setInert(sidebar, false);
      setInert(main, false);
      focusBeforeDrawer = null;
    }
    updateToggle();
  };

  /* --- Events ----------------------------------------------------------- */

  toggle.addEventListener("click", () => {
    if (mobileQuery.matches) {
      if (drawerOpen) {
        closeDrawer();
      } else {
        openDrawer();
      }
      return;
    }
    setCollapsed(!isCollapsed());
  });

  closeButton.addEventListener("click", () => closeDrawer());
  overlay.addEventListener("click", () => closeDrawer());

  sidebar.addEventListener("click", (event) => {
    if (!drawerOpen) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (event.target.closest("a")) closeDrawer({ restoreFocus: false });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !drawerOpen) return;
    event.preventDefault();
    closeDrawer();
  });

  /* Keep Tab inside the open drawer. The main region is inert too, but the
     skip link and other body-level controls should not receive focus while
     the drawer is acting as a modal surface. */
  sidebar.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || !drawerOpen) return;

    const focusable = Array.from(
      sidebar.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')
    ).filter((element) => element.getClientRects().length > 0);
    if (!focusable.length) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  const handleViewportChange = () => applyViewportState();
  if (typeof mobileQuery.addEventListener === "function") {
    mobileQuery.addEventListener("change", handleViewportChange);
  } else {
    mobileQuery.addListener(handleViewportChange);
  }

  applyViewportState();
});


/* ==========================================================================
   5. NOTIFIER (corner toast)
   --------------------------------------------------------------------------
   Small, non-blocking feedback for actions whose result appears away from the
   control that started them (a saved grade, a revoked session). It replaces
   window.alert(), which blocked the page and could not be styled.

   Exposed as window.Campus360Notify so js/auth.js and js/portal.js can use it;
   each toast removes itself after a few seconds and the host is aria-live, so
   assistive technology announces the message once.
   ========================================================================== */

(function () {
  let host = null;

  function notify(message, kind) {
    if (!message) return;

    if (!host) {
      host = document.createElement("div");
      host.className = "app-toast-host";
      host.setAttribute("aria-live", "polite");
      host.setAttribute("aria-atomic", "false");
      document.body.appendChild(host);
    }

    const toast = document.createElement("p");
    toast.className = "app-toast";
    toast.dataset.kind = kind === "error" ? "error" : kind === "success" ? "success" : "info";
    /* Errors are assertive; everything else is polite. */
    toast.setAttribute("role", toast.dataset.kind === "error" ? "alert" : "status");
    toast.textContent = message;
    host.appendChild(toast);

    window.setTimeout(() => {
      toast.remove();
    }, 6000);
  }

  window.Campus360Notify = notify;
})();
