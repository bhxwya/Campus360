"""Canonical department catalogue (core/departments.py).

A DEPARTMENT is the academic unit a faculty member belongs to (e.g.
"Computer Science & Engineering"). It is stored on ``faculty_profiles.department``
(String(120)) which is a free-text column from the original schema.

Why a code-level catalogue instead of a database table?

* The existing schema already stores the department on the faculty profile and
  every read path (admin list, faculty profile) uses that column. Adding a
  ``departments`` table + foreign key would be a migration with a data
  backfill, and the row would still need a display value.
* The catalogue is a POLICY list, not user-created data. Keeping it in code
  means it can be reviewed and versioned, and the API can expose it without a
  seed migration.
* The API accepts a stable CODE (e.g. ``CSE``) or the canonical NAME and
  stores the canonical NAME in the existing column.
  ``FacultyResponse.department_code`` exposes the stable code for clients that
  need an identifier independent of display text.

Existing rows are deliberately NOT rewritten. An unrecognized value is kept
verbatim (and can only be kept, not newly assigned) until an explicit,
approved normalization is run, matching the programme catalogue behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Department:
    """One academic department in the institutional catalogue."""

    code: str
    name: str


#: Proposed initial department catalogue. Derived from the programme catalogue
#: groups and the existing faculty/department values in the schema. This is a
#: STARTING POINT for the institution to confirm - not a claim that every
#: department listed is currently an official academic unit.
DEPARTMENTS: tuple[Department, ...] = (
    Department("CSE", "Computer Science & Engineering"),
    Department("IT", "Information Technology"),
    Department("AIML", "Artificial Intelligence & Machine Learning"),
    Department("DS", "Data Science"),
    Department("ECE", "Electronics & Communication Engineering"),
    Department("EE", "Electrical Engineering"),
    Department("ME", "Mechanical Engineering"),
    Department("CE", "Civil Engineering"),
    Department("COM", "Commerce"),
    Department("MGT", "Management"),
    Department("CA", "Computer Applications"),
    Department("SCI", "Sciences"),
    Department("PHY", "Physics"),
    Department("AH", "Arts & Humanities"),
)


def _key(value: str) -> str:
    """Case/spacing/punctuation-insensitive lookup key."""
    import re

    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


#: Every code and canonical name -> its department. Built at import so a typo
#: that makes two departments share a code fails fast.
_LOOKUP: dict[str, Department] = {}
for _department in DEPARTMENTS:
    for _label in (_department.code, _department.name):
        _lookup_key = _key(_label)
        _existing = _LOOKUP.get(_lookup_key)
        if _existing is not None and _existing is not _department:
            raise RuntimeError(
                f"Department catalogue collision: {_label!r} matches "
                f"{_existing.code} and {_department.code}"
            )
        _LOOKUP[_lookup_key] = _department


def find_department(value: str | None) -> Department | None:
    """Return the department for a code/name, or ``None`` when unknown."""
    if value is None:
        return None
    key = _key(value)
    if not key:
        return None
    return _LOOKUP.get(key)


def canonical_name(value: str | None) -> str | None:
    """Canonical display name for a recognized value, else ``None``."""
    department = find_department(value)
    return department.name if department is not None else None
