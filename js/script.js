/* ==========================================================================
   CAMPUS360 - SHARED SITE BEHAVIOUR (script.js)
   ==========================================================================
   Loaded from the <head> of EVERY page (landing, login, and all dashboards).
   Campus360 is LIGHT-MODE ONLY - there is no theme logic in this file.
   (js/auth.js joins it on the login and dashboard pages only.)

   Behaviour shared by the whole frontend:

      1. Navbar scroll state        -> landing page header compaction
      2. Same-page anchor scrolling  -> landing page in-page navigation
      3. Table search filtering     -> opt-in via [data-table-search]
      4. Filter tab switching       -> opt-in via a .filter-bar container

   Authentication / role routing lives in js/auth.js.

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
   3. CLIENT-SIDE TABLE SEARCH FILTERING
   --------------------------------------------------------------------------
   Opt-in: mark a text input with data-table-search and typing hides the rows
   whose text does not match (case-insensitive substring match). Used today on
   Admin > Students.

   SCOPING - the important detail
   The search box and the table it controls are NOT always siblings. On
   pages/admin/students.html the input sits in its own "Filter and Search Bar"
   <section class="card"> while the table lives in a *separate* card below.
   Scoping to input.closest(".card") therefore finds no rows and the filter
   silently does nothing.

   So the scope is resolved by asking the DOM, not by assuming structure:
     1. start at the input's card,
     2. if that card has no rows, widen to the surrounding .content-wrapper
        (or the document as a last resort),
     3. only bind the listener once rows are actually found.
   ========================================================================== */

onReady(() => {
  document.querySelectorAll("[data-table-search]").forEach((input) => {
    const card = input.closest(".card");
    const scope = card && card.querySelector("tbody tr")
      ? card
      : input.closest(".content-wrapper") || document;

    const rows = scope.querySelectorAll("tbody tr");

    /* Nothing to filter on this page - stay quiet. */
    if (!rows.length) return;

    input.addEventListener("input", () => {
      const query = input.value.trim().toLowerCase();

      rows.forEach((row) => {
        const matches = row.textContent.toLowerCase().includes(query);
        row.hidden = !matches;
      });
    });
  });
});


/* ==========================================================================
   4. FILTER TAB SWITCHING
   --------------------------------------------------------------------------
   The dashboard topbars already ship segmented controls such as
   <span class="filter-tab active">All Notices</span>. They are plain <span>s
   (no buttons, no links), so without a little JavaScript the "active" pill can
   never move.

   This is a visual-only demo control: it moves the .active highlight inside
   its own .filter-bar. It does NOT filter, re-render, or fetch anything -
   the real filtering arrives with the backend. An event listener is attached
   only when a .filter-bar with at least two tabs is present.
   ========================================================================== */

onReady(() => {
  document.querySelectorAll(".filter-bar").forEach((bar) => {
    const tabs = bar.querySelectorAll(".filter-tab");

    if (tabs.length < 2) return;

    /* A single click on the bar (not on a nested icon/span) picks the tab. */
    bar.addEventListener("click", (event) => {
      const clicked = event.target.closest(".filter-tab");

      /* Clicked outside a tab, or on the tab that is already active. */
      if (!clicked || !bar.contains(clicked)) return;

      tabs.forEach((tab) => tab.classList.remove("active"));
      clicked.classList.add("active");
    });
  });
});