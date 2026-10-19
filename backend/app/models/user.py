"""
User accounts and role-specific profiles (models/user.py).

Campus360 separates the LOGIN IDENTITY (User) from role-specific DATA
(StudentProfile / FacultyProfile). Why separate tables instead of one wide
table full of nullable columns?

* Each profile carries only the columns its role needs - no student row
  carrying faculty-only NULLs and vice versa.
* Each profile can grow its own constraints (e.g. unique student_code).
* The account's role is a property of the User; the profile tables hold the
  data only that role needs.

The password_hash column exists from Stage 2 so the schema is ready for
Stage 3 authentication; it will hold an Argon2/bcrypt hash - NEVER plaintext.
"""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import UserRole, pg_enum

if TYPE_CHECKING:  # pragma: no cover - import-cycle guards for type hints only
    from app.models.course import Course


class User(TimestampMixin, Base):
    """One login account, whatever the role.

    email is UNIQUE because it is the login identifier: the database (not
    only application code) must refuse a second account with the same email,
    even if a bug or a race condition tries to create one.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # Lowercased copy of email enforcing CASE-INSENSITIVE uniqueness.
    # PostgreSQL UNIQUE on a plain column IS case-sensitive ('A@x' != 'a@x'),
    # which would otherwise allow duplicate accounts differing only by case.
    # Stage 3's service lowercases the email before INSERT.
    #
    # The CHECK constraint below (email_normalized_lowercase) makes that
    # lowercase promise a DATABASE rule, not just an application habit: any
    # row where email_normalized differs from lower(email) is rejected here,
    # so a buggy/forgotten lowercase can never smuggle in a second account
    # that differs only by case.
    email_normalized: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    password_hash: Mapped[str] = mapped_column(
        String(255),  # Argon2/bcrypt hashes fit well under 255 chars
        nullable=False,
    )

    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole), nullable=False, index=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        # Deactivation instead of deletion: academic records keep referencing
        # the user's profile, so real-world "removal" flips this flag instead.
    )

    __table_args__ = (
        # email_normalized MUST be the lowercase form of email. This is the
        # hardening that turns the "we lowercase before insert" convention
        # into something PostgreSQL itself enforces. Named deterministically
        # by the metadata convention as ck_users_email_normalized_lowercase.
        CheckConstraint("email_normalized = lower(email)", name="email_normalized_lowercase"),
    )

    # --- Relationships -----------------------------------------------------
    # uselist=False because a User has AT MOST ONE profile of each kind -
    # the Python-side counterpart of the unique FK on the profile side.
    # "StudentProfile" is a string to avoid a circular import; SQLAlchemy
    # resolves it after all model modules are loaded (see models/__init__.py).

    student_profile: Mapped[StudentProfile | None] = relationship(
        back_populates="user",
        uselist=False,
        # If the account is deleted, delete its profile too: a profile with
        # no login would be unreachable and meaningless.
        cascade="all, delete-orphan",
        passive_deletes=True,  # let PostgreSQL cascade, not Python loops
    )

    faculty_profile: Mapped[FacultyProfile | None] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StudentProfile(TimestampMixin, Base):
    """Student-specific data, one row per student, tightly bound to a User.

    ``student_code`` is the human-visible "Roll / Scholar ID" from the admin
    UI (e.g. ST-2026-8941). The FORMAT is not enforced here - the institution
    may change it - only uniqueness and presence are.
    """

    __tablename__ = "student_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one profile per account - the 1:1 constraint
    )

    student_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,  # no two students may share a roll/scholar ID
        index=True,  # roll number is a common search key on admin pages
    )

    full_name: Mapped[str] = mapped_column(String(120), nullable=False)

    # --- Fields grounded in the admin "Students" table columns -------------
    degree_program: Mapped[str | None] = mapped_column(String(120))  # e.g. "B.Tech CSE"
    current_semester: Mapped[int | None] = mapped_column()  # SMALLINT by SQLAlchemy default
    phone: Mapped[str | None] = mapped_column(String(20))

    # --- Relationships -----------------------------------------------------
    user: Mapped[User] = relationship(back_populates="student_profile")

    enrollments: Mapped[list[Enrollment]] = relationship(  # noqa: F821
        back_populates="student",
        # Deleting a profile that has enrollments must FAIL at the database
        # (the FK is RESTRICT): academic history is never cascaded away.
        passive_deletes=True,
    )


class FacultyProfile(TimestampMixin, Base):
    """Faculty-specific data, one row per faculty member, bound to a User.

    ``faculty_code`` mirrors the "Faculty ID" column of the admin UI.
    """

    __tablename__ = "faculty_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    faculty_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
    )

    full_name: Mapped[str] = mapped_column(String(120), nullable=False)

    department: Mapped[str | None] = mapped_column(String(120))  # UI "Department" column
    designation: Mapped[str | None] = mapped_column(String(120))  # UI "Designation"

    # --- Relationships -----------------------------------------------------
    user: Mapped[User] = relationship(back_populates="faculty_profile")

    led_courses: Mapped[list[Course]] = relationship(  # noqa: F821
        back_populates="lead_faculty",
        # The actual SET NULL policy lives on the FK (Course.lead_faculty_id);
        # this relationship simply loads the courses this faculty leads.
    )
