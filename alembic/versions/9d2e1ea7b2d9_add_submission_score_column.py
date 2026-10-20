"""assignment max_score + full submission grading columns

Revision ID: 9d2e1ea7b2d9
Revises: 1527f5e145ba
Create Date: 2026-10-20 03:00:00.000000

Rewritten IN PLACE (revision id and down_revision preserved): inspection
confirmed this revision had been applied ONLY to campus360_test (never to
campus360_db) and the file was untracked in git, so amending it rewrites no
committed or shared history. The test database is remediated separately by
rebuilding its schema; no legacy-data UPDATE is included because both
databases hold zero assignment rows (a replay of this revision never meets
an existing graded row).

Design (mirrors backend/app/models/assignment.py):

* assignments.max_score - maximum attainable score; NOT NULL, server
  default 100 (the value existing rows are backfilled with; matches the ORM
  default), CHECK > 0.
* assignment_submissions - score, max_score (grading-time snapshot),
  feedback, graded_by (FK -> faculty_profiles.id ON DELETE SET NULL) and
  graded_at. CHECKs bound score by the SNAPSHOT (never by a hardcoded 100,
  since an assignment may allow scores above 100) and enforce the grading
  state machine: submitted rows carry no grade fields; graded/returned rows
  must carry score + snapshot + graded_at.

Constraint names are passed through op.f() as the FULL names SQLAlchemy's
naming convention (NAMING_CONVENTION in app/db/base.py) resolves, so
alembic autogenerate sees model metadata and the live database in agreement.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d2e1ea7b2d9'
down_revision: Union[str, Sequence[str], None] = '1527f5e145ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add assignments.max_score and the submission grading columns."""
    # --- assignments: per-assignment grading maximum ----------------------
    op.add_column(
        'assignments',
        sa.Column(
            'max_score',
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default=sa.text('100'),
        ),
    )
    op.create_check_constraint(
        op.f('ck_assignments_max_score_positive'),
        'assignments',
        'max_score > 0',
    )

    # --- assignment_submissions: grading columns --------------------------
    op.add_column(
        'assignment_submissions',
        sa.Column('score', sa.Numeric(precision=5, scale=2), nullable=True),
    )
    op.add_column(
        'assignment_submissions',
        sa.Column('max_score', sa.Numeric(precision=5, scale=2), nullable=True),
    )
    op.add_column(
        'assignment_submissions',
        sa.Column('feedback', sa.Text(), nullable=True),
    )
    op.add_column(
        'assignment_submissions',
        sa.Column('graded_by', sa.Integer(), nullable=True),
    )
    op.add_column(
        'assignment_submissions',
        sa.Column('graded_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f('fk_assignment_submissions_graded_by_faculty_profiles'),
        'assignment_submissions',
        'faculty_profiles',
        ['graded_by'],
        ['id'],
        ondelete='SET NULL',
    )

    # NOTE: no drop of a pre-existing "score <= 100" CHECK - on every
    # database this migration can run against (fresh replays and dev at
    # 1527f5e145ba) no such constraint exists; only the abandoned first
    # version of this same revision created it, and the database carrying
    # that is rebuilt rather than upgraded.
    op.create_check_constraint(
        op.f('ck_assignment_submissions_submission_score_range'),
        'assignment_submissions',
        'score IS NULL OR (max_score IS NOT NULL AND score >= 0 AND score <= max_score)',
    )
    op.create_check_constraint(
        op.f('ck_assignment_submissions_submission_max_score_positive'),
        'assignment_submissions',
        'max_score IS NULL OR max_score > 0',
    )
    # Lowercase literals = the values pg_enum stores for submissionstatus.
    op.create_check_constraint(
        op.f('ck_assignment_submissions_submission_grade_state'),
        'assignment_submissions',
        "status = 'submitted' AND score IS NULL AND max_score IS NULL AND graded_at IS NULL"
        " OR status IN ('graded', 'returned') AND score IS NOT NULL AND max_score IS NOT NULL"
        ' AND graded_at IS NOT NULL',
    )


def downgrade() -> None:
    """Remove the grading columns and restore the pre-grading schema."""
    op.drop_constraint(
        op.f('ck_assignment_submissions_submission_grade_state'),
        'assignment_submissions',
        type_='check',
    )
    op.drop_constraint(
        op.f('ck_assignment_submissions_submission_max_score_positive'),
        'assignment_submissions',
        type_='check',
    )
    op.drop_constraint(
        op.f('ck_assignment_submissions_submission_score_range'),
        'assignment_submissions',
        type_='check',
    )
    op.drop_constraint(
        op.f('fk_assignment_submissions_graded_by_faculty_profiles'),
        'assignment_submissions',
        type_='foreignkey',
    )
    op.drop_column('assignment_submissions', 'graded_at')
    op.drop_column('assignment_submissions', 'graded_by')
    op.drop_column('assignment_submissions', 'feedback')
    op.drop_column('assignment_submissions', 'max_score')
    op.drop_column('assignment_submissions', 'score')
    op.drop_constraint(
        op.f('ck_assignments_max_score_positive'),
        'assignments',
        type_='check',
    )
    op.drop_column('assignments', 'max_score')