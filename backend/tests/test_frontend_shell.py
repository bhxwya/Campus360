"""Source-level contracts for the shared application shell.

The project intentionally has no JavaScript test runner, so these tests pin the
shared markup, stylesheet hooks and storage/sidebar behaviour that all three
portals rely on. They are deliberately role-parametrised: a regression in one
portal's navigation must not be hidden by another portal's passing assertions.
"""

import html
import re
from pathlib import Path

import pytest
from app.core.config import PROJECT_ROOT

PAGES = PROJECT_ROOT / "pages"
SCRIPT = PROJECT_ROOT / "js" / "script.js"
DASHBOARD_CSS = PROJECT_ROOT / "css" / "dashboard.css"
PORTAL_JS = PROJECT_ROOT / "js" / "portal.js"

NAVIGATION = {
    "admin": {
        "dashboard.html": "Dashboard",
        "students.html": "Students",
        "faculty.html": "Faculty",
        "courses.html": "Courses",
        "notices.html": "Notices",
        "reports.html": "Reports",
    },
    "student": {
        "dashboard.html": "Dashboard",
        "attendance.html": "Attendance",
        "timetable.html": "Timetable",
        "assignments.html": "Assignments",
        "marks-results.html": "Marks & Results",
        "announcements.html": "Announcements",
        "academic-records.html": "Academic Records",
    },
    "faculty": {
        "dashboard.html": "Dashboard",
        "attendance.html": "Attendance",
        "assignments.html": "Assignments",
        "marks.html": "Marks",
        "announcements.html": "Announcements",
    },
}

PORTAL_FILES = [
    (role, path) for role in NAVIGATION for path in sorted((PAGES / role).glob("*.html"))
]


def _nav_links(source: str) -> list[tuple[str, str, bool]]:
    """Return (href, label, active) for every sidebar navigation link."""
    links = []
    pattern = re.compile(
        r'<a href="([^"]+)" class="nav-link([^"]*)">(.*?)</a>',
        re.DOTALL,
    )
    for match in pattern.finditer(source):
        href, classes, body = match.groups()
        label = re.search(r"<span>([^<]+)</span>", body)
        links.append(
            (
                html.unescape(href),
                html.unescape(label.group(1).strip()) if label else "",
                "active" in classes.split(),
            )
        )
    return links


@pytest.mark.parametrize(("role", "path"), PORTAL_FILES)
def test_each_portal_page_uses_the_shared_shell_and_role_navigation(role: str, path: Path) -> None:
    """Every role page shares the frame and keeps its own menu untouched."""
    source = path.read_text(encoding="utf-8")
    expected = NAVIGATION[role]
    links = _nav_links(source)

    assert 'class="app-layout"' in source
    assert 'class="app-sidebar"' in source
    assert 'class="app-topbar"' in source
    assert 'class="app-main"' in source
    assert f'class="brand-badge {role}"' in source
    assert "../../js/script.js" in source

    assert [href for href, _, _ in links] == list(expected)
    assert {label for _, label, _ in links} == set(expected.values())
    assert [href for href, _, active in links if active] == [path.name]


def test_sidebar_preference_is_applied_before_the_dom_and_persisted() -> None:
    """Collapsed state is restored in <head> and saved after every toggle."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const CAMPUS360_SIDEBAR_STORAGE_KEY = "campus360.sidebarCollapsed";' in source
    assert "window.localStorage.getItem(CAMPUS360_SIDEBAR_STORAGE_KEY)" in source
    assert 'document.documentElement.classList.add("sidebar-collapsed")' in source
    assert "window.localStorage.setItem(CAMPUS360_SIDEBAR_STORAGE_KEY, String(collapsed))" in source
    assert 'root.classList.toggle("sidebar-collapsed", collapsed)' in source
    assert 'toggle.setAttribute("aria-expanded", String(expanded))' in source
    assert 'toggle.setAttribute("title", label)' in source


def test_mobile_sidebar_is_a_focus_managed_drawer() -> None:
    """The shared drawer closes on links, Escape, close and overlay clicks."""
    source = SCRIPT.read_text(encoding="utf-8")
    css = DASHBOARD_CSS.read_text(encoding="utf-8")

    assert 'window.matchMedia("(max-width: 768px)")' in source
    assert 'root.classList.add("sidebar-drawer-open")' in source
    assert 'root.classList.remove("sidebar-drawer-open")' in source
    assert "closeDrawer({ restoreFocus: false })" in source
    assert 'event.key !== "Escape"' in source
    assert 'overlay.addEventListener("click", () => closeDrawer())' in source
    assert 'closeButton.addEventListener("click", () => closeDrawer())' in source
    assert "setInert(main, true)" in source
    assert "setInert(sidebar, true)" in source
    assert 'toggle.setAttribute("aria-controls", sidebar.id)' in source
    assert 'closeButton.setAttribute("aria-label", "Close navigation menu")' in source

    assert "@media (max-width: 768px)" in css
    assert "transform: translateX(-100%)" in css
    assert "html.sidebar-drawer-open .app-sidebar" in css
    assert "html.sidebar-drawer-open .sidebar-overlay" in css
    assert "html.sidebar-drawer-open,\n  html.sidebar-drawer-open body" in css
    assert "overflow: hidden;" in css
    assert "width: min(320px, 86vw);" in css


def test_student_details_dialog_contract_is_compact_and_accessible() -> None:
    """Pin the details dialog's fields, empty state, scrolling and close control."""
    portal_source = PORTAL_JS.read_text(encoding="utf-8")
    start = portal_source.index("async function showStudentDetails")
    end = portal_source.index("async function adminList", start)
    details_block = portal_source[start:end]
    css = DASHBOARD_CSS.read_text(encoding="utf-8")

    for label in (
        "Student ID",
        "Roll Number",
        "Full name",
        "Institutional email",
        "Phone number",
        "Degree programme",
        "Current semester",
        "Account status",
        "Enrolled courses",
    ):
        assert label in details_block

    assert "No course enrollments are recorded." in details_block
    assert "Enrollment data is unavailable." in details_block
    assert 'class="student-details-count"' in details_block
    assert 'class="student-detail email wide"' in details_block
    assert 'aria-label="Close student details"' in details_block
    assert 'setAttribute("aria-labelledby", "student-details-title")' in details_block
    assert 'id="student-details-title"' in details_block
    assert 'root.classList.add("dialog-open")' in details_block
    assert 'root.classList.remove("dialog-open")' in details_block
    assert 'dialog.addEventListener("cancel"' in details_block
    assert "opener.focus()" in details_block

    assert ".student-details-body" in css
    assert "overflow-y: auto;" in css
    assert "max-height: calc(100dvh - 24px);" in css
    assert "overflow-wrap: break-word;" in css
    assert "word-break: normal;" in css
    assert "html.dialog-open" in css
