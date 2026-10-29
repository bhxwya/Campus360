"""Canonical academic programme catalogue (core/programmes.py).

A PROGRAMME (B.Tech CSE) is not a COURSE (CS301 Data Structures). Courses live
in the ``courses`` table; programmes are the branch/degree a student belongs
to and are stored on ``student_profiles.degree_program`` (String(120)).

Why a code-level catalogue instead of a database table?

* The existing schema already stores the programme on the student profile and
  every read path (admin list, student profile, reports) uses that column.
  Adding a ``programmes`` table + foreign key would be a migration with a data
  backfill, and the student row would still need a display value.
* The catalogue is a POLICY list, not user-created data. Keeping it in code
  means it can be reviewed and versioned, and the API can expose it without a
  seed migration.
* The API accepts a stable CODE (``BTECH-CSE``), the canonical NAME, or a
  common alias (``CSE``, ``B.Tech CSE``) and stores the canonical NAME in the
  existing column. ``StudentResponse.programme_code`` exposes the stable code
  for clients that need an identifier independent of display text.

Existing rows are deliberately NOT rewritten. A recognized legacy value is
resolved for filtering and for the edit form; an unrecognized value is kept
verbatim (and can only be kept, not newly assigned) until an explicit,
approved normalization is run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ENGINEERING = "Engineering"
_COMMERCE = "Commerce and Management"
_APPLICATIONS = "Computer Applications"
_SCIENCE = "Science and Humanities"


@dataclass(frozen=True)
class SemesterPolicy:
    """Valid semester bounds for the current programme structure.

    Every programme in :data:`PROGRAMMES` currently uses this rule. A future
    programme with a different duration (for example a research programme)
    should add its own explicit policy and select it by programme code; it
    must not widen this default for existing programmes.
    """

    minimum: int = 1
    maximum: int = 14

    def __post_init__(self) -> None:
        if self.minimum < 1:
            raise ValueError("minimum semester must be at least 1")
        if self.maximum < self.minimum:
            raise ValueError("maximum semester must be >= minimum semester")

    @property
    def options(self) -> tuple[int, ...]:
        """Every valid semester, ascending, for UI option generation."""
        return tuple(range(self.minimum, self.maximum + 1))


#: Default semester rule for every programme in the current catalogue.
SEMESTER_POLICY = SemesterPolicy()


@dataclass(frozen=True)
class Programme:
    """One degree programme/branch in the institutional catalogue."""

    code: str
    name: str
    group: str
    aliases: tuple[str, ...] = ()


#: The proposed initial catalogue. It is a STARTING POINT for the institution
#: to confirm - not a claim that every programme is currently offered.
PROGRAMMES: tuple[Programme, ...] = (
    # --- Engineering ------------------------------------------------------
    Programme(
        "BTECH-CSE",
        "B.Tech Computer Science and Engineering (CSE)",
        _ENGINEERING,
        (
            "CSE",
            "B.Tech CSE",
            "BTech CSE",
            "Computer Science",
            "Computer Science and Engineering",
            "B.Tech Computer Science and Engineering",
        ),
    ),
    Programme(
        "BTECH-IT",
        "B.Tech Information Technology (IT)",
        _ENGINEERING,
        ("IT", "B.Tech IT", "BTech IT", "Information Technology", "B.Tech Information Technology"),
    ),
    Programme(
        "BTECH-AIML",
        "B.Tech Artificial Intelligence and Machine Learning (AI/ML)",
        _ENGINEERING,
        (
            "AI/ML",
            "AI ML",
            "AIML",
            "B.Tech AI/ML",
            "Artificial Intelligence and Machine Learning",
        ),
    ),
    Programme(
        "BTECH-DS",
        "B.Tech Data Science (DS)",
        _ENGINEERING,
        ("DS", "B.Tech DS", "Data Science"),
    ),
    Programme(
        "BTECH-ECE",
        "B.Tech Electronics and Communication Engineering (ECE)",
        _ENGINEERING,
        (
            "ECE",
            "B.Tech ECE",
            "Electronics and Communication Engineering",
        ),
    ),
    Programme(
        "BTECH-EE",
        "B.Tech Electrical Engineering (EE)",
        _ENGINEERING,
        ("EE", "B.Tech EE", "Electrical Engineering"),
    ),
    Programme(
        "BTECH-ME",
        "B.Tech Mechanical Engineering (ME)",
        _ENGINEERING,
        ("ME", "B.Tech ME", "Mechanical Engineering"),
    ),
    Programme(
        "BTECH-CE",
        "B.Tech Civil Engineering (CE)",
        _ENGINEERING,
        ("CE", "B.Tech CE", "Civil Engineering"),
    ),
    # --- Commerce and Management -----------------------------------------
    Programme("BCOM", "B.Com", _COMMERCE, ("BCom", "B.Com.", "Bachelor of Commerce")),
    Programme(
        "BCOM-HONS",
        "B.Com (Honours)",
        _COMMERCE,
        ("BCom Hons", "B.Com Hons", "B.Com (Hons)", "Bachelor of Commerce (Honours)"),
    ),
    Programme("BBA", "BBA", _COMMERCE, ("B.B.A.", "Bachelor of Business Administration")),
    Programme("MBA", "MBA", _COMMERCE, ("M.B.A.", "Master of Business Administration")),
    # --- Computer Applications -------------------------------------------
    Programme("BCA", "BCA", _APPLICATIONS, ("B.C.A.", "Bachelor of Computer Applications")),
    Programme("MCA", "MCA", _APPLICATIONS, ("M.C.A.", "Master of Computer Applications")),
    # --- Science and Humanities ------------------------------------------
    Programme("BSC", "B.Sc", _SCIENCE, ("BSc", "B.Sc.", "Bachelor of Science")),
    Programme("MSC", "M.Sc", _SCIENCE, ("MSc", "M.Sc.", "Master of Science")),
    Programme("BA", "BA", _SCIENCE, ("B.A.", "Bachelor of Arts")),
    Programme("MA", "MA", _SCIENCE, ("M.A.", "Master of Arts")),
)


def _key(value: str) -> str:
    """Case/spacing/punctuation-insensitive lookup key.

    ``cse``, ``CSE``, ``B.Tech CSE`` and ``B-Tech  CSE`` are therefore the same
    programme. Keeping the comparison here (not in the database) is what lets
    legacy display strings resolve without rewriting them.
    """
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


#: Every code, canonical name and alias -> its programme. Built at import so a
#: typo that makes two programmes share an alias fails fast instead of silently
#: resolving to the wrong one.
_LOOKUP: dict[str, Programme] = {}
for _programme in PROGRAMMES:
    for _label in (_programme.code, _programme.name, *_programme.aliases):
        _lookup_key = _key(_label)
        _existing = _LOOKUP.get(_lookup_key)
        if _existing is not None and _existing is not _programme:
            raise RuntimeError(
                f"Programme catalogue collision: {_label!r} matches "
                f"{_existing.code} and {_programme.code}"
            )
        _LOOKUP[_lookup_key] = _programme


def find_programme(value: str | None) -> Programme | None:
    """Return the programme for a code/name/alias, or ``None`` when unknown."""
    if value is None:
        return None
    key = _key(value)
    if not key:
        return None
    return _LOOKUP.get(key)


def canonical_name(value: str | None) -> str | None:
    """Canonical display name for a recognized value, else ``None``."""
    programme = find_programme(value)
    return programme.name if programme is not None else None


def programme_code(value: str | None) -> str | None:
    """Stable catalogue code for a recognized value, else ``None``."""
    programme = find_programme(value)
    return programme.code if programme is not None else None


def match_values(programme: Programme) -> tuple[str, ...]:
    """Every stored spelling that means this programme.

    Used by list filters so a legacy row stored as ``B.Tech CSE`` is found when
    the admin filters by the canonical ``B.Tech Computer Science and
    Engineering (CSE)`` (or by the stable code).
    """
    return tuple({programme.name, programme.code, *programme.aliases})
