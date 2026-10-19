"""
Announcements / notices (models/announcement.py).

An ``Announcement`` is a notice published by an admin or faculty member.
Its audience, category and lifecycle states are all enums taken from the
existing Admin > Notices page, so the schema mirrors what the UI already
displays (Target Audience, Category, Active/Archived status).

Deletion: ``created_by`` is SET NULL - notices are public information that
should survive the publisher's account. Announcements themselves are archived
(status=ARCHIVED) rather than deleted, consistent with the project-wide
"preserve history" policy.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
    pg_enum,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.models.user import User


class Announcement(TimestampMixin, Base):
    """One broadcast notice with a target audience and publication status."""

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    category: Mapped[AnnouncementCategory] = mapped_column(
        pg_enum(AnnouncementCategory),
        nullable=False,
    )

    audience: Mapped[AnnouncementAudience] = mapped_column(
        pg_enum(AnnouncementAudience),
        nullable=False,
    )

    status: Mapped[AnnouncementStatus] = mapped_column(
        pg_enum(AnnouncementStatus),
        default=AnnouncementStatus.ACTIVE,
        nullable=False,
        index=True,  # feeds filter by Active vs Archived constantly
    )

    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        # NULL means "published by a since-removed account" - still valid.
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        # Nullable because a notice can exist as a draft (unpublished) before
        # an admin broadcasts it; Stage 4 sets this when publishing.
        index=True,  # feeds sort by recency
    )

    # --- Relationships -----------------------------------------------------
    author: Mapped[User | None] = relationship()
