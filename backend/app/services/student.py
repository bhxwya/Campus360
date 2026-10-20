"""
Student business logic (backend/app/services/student.py).

Stage 7A read-only operations for the student portal. Rules:
1. Strict self-service isolation: queries are anchored exclusively to the
   authenticated user's StudentProfile. A client-supplied student ID is never
   accepted to determine access.
2. Safe zero-handling: zero attendance sessions produce a null percentage,
   never a ZeroDivisionError.
3. Incomplete marks handling: final grades are never calculated from incomplete
   components; missing or partial records are clearly distinguished.
4. N+1 prevention: use eager loading (joinedload) and SQL aggregations.
"""

from datetime import UTC, datetime

from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload

from app.models.announcement import Announcement
from app.models.assignment import Assignment, AssignmentSubmission
from app.models.attendance import AttendanceRecord
from app.models.course import Course, Enrollment
from app.models.enums import (
    AnnouncementAudience,
    AnnouncementStatus,
    AttendanceStatus,
    EnrollmentStatus,
)
from app.models.mark import Mark
from app.models.timetable import TimetableEntry
from app.models.user import StudentProfile
from app.schemas.faculty import compute_grade
from app.schemas.student import (
    AttendanceSummaryResponse,
    CourseAttendanceSummary,
    CourseMarkSummary,
    DashboardResponse,
    MarksSummaryResponse,
    MyAttendanceRecordResponse,
    MyEnrollmentResponse,
    MyTimetableEntryResponse,
    StudentAnnouncementResponse,
    StudentAssignmentResponse,
    StudentMarkResponse,
    StudentProfileResponse,
)

ATTENDED_STATUSES = (AttendanceStatus.PRESENT, AttendanceStatus.LATE)


class StudentError(Exception):
    """Raised when a student operation cannot proceed.

    Carries the HTTP status code the router should answer with.
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _require_student_profile(db: Session, user_id: int) -> StudentProfile:
    """Resolve the StudentProfile for this user, or raise StudentError 404."""
    profile = (
        db.query(StudentProfile)
        .filter(StudentProfile.user_id == user_id)
        .options(joinedload(StudentProfile.user))
        .one_or_none()
    )
    if profile is None:
        raise StudentError("Student profile not found", 404)
    return profile


def get_profile(db: Session, user_id: int) -> StudentProfileResponse:
    """Return the authenticated student's profile and account metadata."""
    profile = _require_student_profile(db, user_id)
    return StudentProfileResponse(
        id=profile.id,
        student_code=profile.student_code,
        full_name=profile.full_name,
        degree_program=profile.degree_program,
        current_semester=profile.current_semester,
        phone=profile.phone,
        email=profile.user.email,
        is_active=profile.user.is_active,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def list_courses(db: Session, user_id: int) -> list[MyEnrollmentResponse]:
    """Return all course enrollments (current and historical) for the student."""
    profile = _require_student_profile(db, user_id)
    enrollments = (
        db.query(Enrollment)
        .filter(Enrollment.student_id == profile.id)
        .options(
            joinedload(Enrollment.course).joinedload(Course.lead_faculty)
        )
        .order_by(Enrollment.id.asc())
        .all()
    )
    return [
        MyEnrollmentResponse(
            enrollment_id=e.id,
            status=e.status,
            course_id=e.course.id,
            course_code=e.course.code,
            course_title=e.course.title,
            credits=e.course.credits,
            department=e.course.department,
            lead_faculty_name=(
                e.course.lead_faculty.full_name if e.course.lead_faculty else None
            ),
        )
        for e in enrollments
    ]


def get_timetable(db: Session, user_id: int) -> list[MyTimetableEntryResponse]:
    """Return recurring weekly timetable entries for active enrolled courses."""
    profile = _require_student_profile(db, user_id)
    active_course_ids = [
        row[0]
        for row in db.query(Enrollment.course_id)
        .filter(
            Enrollment.student_id == profile.id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
        .all()
    ]
    if not active_course_ids:
        return []

    entries = (
        db.query(TimetableEntry)
        .filter(TimetableEntry.course_id.in_(active_course_ids))
        .options(
            joinedload(TimetableEntry.course),
            joinedload(TimetableEntry.faculty),
        )
        .order_by(TimetableEntry.day_of_week.asc(), TimetableEntry.start_time.asc())
        .all()
    )
    return [
        MyTimetableEntryResponse(
            id=entry.id,
            course_id=entry.course_id,
            course_code=entry.course.code,
            course_title=entry.course.title,
            day_of_week=entry.day_of_week,
            start_time=entry.start_time,
            end_time=entry.end_time,
            room=entry.room,
            faculty_name=entry.faculty.full_name if entry.faculty else None,
        )
        for entry in entries
    ]


def get_attendance_summary(db: Session, user_id: int) -> AttendanceSummaryResponse:
    """Compute per-course and overall attendance statistics for the student."""
    profile = _require_student_profile(db, user_id)
    enrollments = (
        db.query(Enrollment)
        .filter(Enrollment.student_id == profile.id)
        .options(
            joinedload(Enrollment.course).joinedload(Course.lead_faculty)
        )
        .order_by(Enrollment.id.asc())
        .all()
    )
    if not enrollments:
        return AttendanceSummaryResponse(overall_percent=None, courses=[])

    course_ids = [e.course_id for e in enrollments]

    stats_rows = (
        db.query(
            TimetableEntry.course_id,
            func.count(AttendanceRecord.id).label("total"),
            func.count(
                case((AttendanceRecord.status == AttendanceStatus.PRESENT, 1))
            ).label("present"),
            func.count(
                case((AttendanceRecord.status == AttendanceStatus.LATE, 1))
            ).label("late"),
            func.count(
                case((AttendanceRecord.status == AttendanceStatus.ABSENT, 1))
            ).label("absent"),
        )
        .join(AttendanceRecord, AttendanceRecord.timetable_entry_id == TimetableEntry.id)
        .filter(
            AttendanceRecord.student_id == profile.id,
            TimetableEntry.course_id.in_(course_ids),
        )
        .group_by(TimetableEntry.course_id)
        .all()
    )

    stats_by_course = {
        row.course_id: {
            "total": row.total,
            "present": row.present,
            "late": row.late,
            "absent": row.absent,
        }
        for row in stats_rows
    }

    total_all_sessions = 0
    total_attended_all = 0
    course_summaries = []

    for e in enrollments:
        c = e.course
        c_stats = stats_by_course.get(
            c.id, {"total": 0, "present": 0, "late": 0, "absent": 0}
        )
        tot = c_stats["total"]
        pres = c_stats["present"]
        late = c_stats["late"]
        ab = c_stats["absent"]

        total_all_sessions += tot
        total_attended_all += (pres + late)

        if tot > 0:
            pct = round((pres + late) * 100.0 / tot, 2)
            if pct >= 85.0:
                c_status = "Good"
            elif pct >= 75.0:
                c_status = "Watch"
            else:
                c_status = "Critical"
        else:
            pct = None
            c_status = "No Data"

        course_summaries.append(
            CourseAttendanceSummary(
                course_id=c.id,
                course_code=c.code,
                course_title=c.title,
                faculty_name=(
                    c.lead_faculty.full_name if c.lead_faculty else None
                ),
                total_sessions=tot,
                present_count=pres,
                late_count=late,
                absent_count=ab,
                attendance_percent=pct,
                status=c_status,
            )
        )

    overall_pct = (
        round(total_attended_all * 100.0 / total_all_sessions, 2)
        if total_all_sessions > 0
        else None
    )
    return AttendanceSummaryResponse(
        overall_percent=overall_pct, courses=course_summaries
    )


def list_attendance_records(
    db: Session, user_id: int, course_id: int | None = None
) -> list[MyAttendanceRecordResponse]:
    """Return raw attendance records for the student, optionally filtered by course."""
    profile = _require_student_profile(db, user_id)

    if course_id is not None:
        is_enrolled = (
            db.query(Enrollment.id)
            .filter(
                Enrollment.student_id == profile.id,
                Enrollment.course_id == course_id,
            )
            .first()
        )
        if not is_enrolled:
            raise StudentError(f"Not enrolled in course {course_id}", 403)

    query = (
        db.query(AttendanceRecord)
        .join(AttendanceRecord.timetable_entry)
        .join(TimetableEntry.course)
        .filter(AttendanceRecord.student_id == profile.id)
        .options(
            joinedload(AttendanceRecord.timetable_entry).joinedload(
                TimetableEntry.course
            )
        )
    )
    if course_id is not None:
        query = query.filter(TimetableEntry.course_id == course_id)

    records = query.order_by(
        AttendanceRecord.attendance_date.desc(),
        TimetableEntry.start_time.asc(),
    ).all()

    return [
        MyAttendanceRecordResponse(
            id=r.id,
            course_id=r.timetable_entry.course_id,
            course_code=r.timetable_entry.course.code,
            timetable_entry_id=r.timetable_entry_id,
            day_of_week=r.timetable_entry.day_of_week,
            attendance_date=r.attendance_date,
            status=r.status,
        )
        for r in records
    ]


def list_assignments(db: Session, user_id: int) -> list[StudentAssignmentResponse]:
    """Return active assignments and any submitted work for enrolled courses."""
    profile = _require_student_profile(db, user_id)
    active_course_ids = [
        row[0]
        for row in db.query(Enrollment.course_id)
        .filter(
            Enrollment.student_id == profile.id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
        .all()
    ]
    if not active_course_ids:
        return []

    rows = (
        db.query(Assignment, AssignmentSubmission, Course)
        .join(Course, Course.id == Assignment.course_id)
        .outerjoin(
            AssignmentSubmission,
            (AssignmentSubmission.assignment_id == Assignment.id)
            & (AssignmentSubmission.student_id == profile.id),
        )
        .filter(
            Assignment.course_id.in_(active_course_ids),
            Assignment.is_active.is_(True),
        )
        .order_by(Assignment.due_at.asc(), Assignment.id.asc())
        .all()
    )

    results = []
    for assignment, submission, course in rows:
        results.append(
            StudentAssignmentResponse(
                id=assignment.id,
                course_id=assignment.course_id,
                course_code=course.code,
                course_title=course.title,
                title=assignment.title,
                description=assignment.description,
                due_at=assignment.due_at,
                weightage_percent=(
                    float(assignment.weightage_percent)
                    if assignment.weightage_percent is not None
                    else None
                ),
                max_score=float(assignment.max_score),
                is_active=assignment.is_active,
                submission_id=submission.id if submission else None,
                submission_status=submission.status if submission else None,
                submitted_at=submission.submitted_at if submission else None,
                content=submission.content if submission else None,
                score=(
                    float(submission.score)
                    if submission and submission.score is not None
                    else None
                ),
                feedback=submission.feedback if submission else None,
                graded_at=submission.graded_at if submission else None,
            )
        )
    return results


def get_assignment(
    db: Session, user_id: int, assignment_id: int
) -> StudentAssignmentResponse:
    """Return a single assignment and the student's submission."""
    profile = _require_student_profile(db, user_id)
    assignment = (
        db.query(Assignment)
        .options(joinedload(Assignment.course))
        .filter(Assignment.id == assignment_id)
        .one_or_none()
    )
    if assignment is None:
        raise StudentError(f"Assignment {assignment_id} not found", 404)

    is_enrolled = (
        db.query(Enrollment.id)
        .filter(
            Enrollment.student_id == profile.id,
            Enrollment.course_id == assignment.course_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
        .first()
    )
    if not is_enrolled:
        raise StudentError(
            f"Not enrolled in course for assignment {assignment_id}", 403
        )

    submission = (
        db.query(AssignmentSubmission)
        .filter(
            AssignmentSubmission.assignment_id == assignment.id,
            AssignmentSubmission.student_id == profile.id,
        )
        .one_or_none()
    )

    return StudentAssignmentResponse(
        id=assignment.id,
        course_id=assignment.course_id,
        course_code=assignment.course.code,
        course_title=assignment.course.title,
        title=assignment.title,
        description=assignment.description,
        due_at=assignment.due_at,
        weightage_percent=(
            float(assignment.weightage_percent)
            if assignment.weightage_percent is not None
            else None
        ),
        max_score=float(assignment.max_score),
        is_active=assignment.is_active,
        submission_id=submission.id if submission else None,
        submission_status=submission.status if submission else None,
        submitted_at=submission.submitted_at if submission else None,
        content=submission.content if submission else None,
        score=(
            float(submission.score)
            if submission and submission.score is not None
            else None
        ),
        feedback=submission.feedback if submission else None,
        graded_at=submission.graded_at if submission else None,
    )


def list_marks(db: Session, user_id: int) -> list[StudentMarkResponse]:
    """Return all scored assessment components for the student."""
    profile = _require_student_profile(db, user_id)
    marks = (
        db.query(Mark)
        .options(joinedload(Mark.course))
        .filter(Mark.student_id == profile.id)
        .join(Mark.course)
        .order_by(Course.code.asc(), Mark.component.asc())
        .all()
    )
    responses = []
    for m in marks:
        pct = round(float(m.score) * 100.0 / float(m.max_score), 2)
        grade, grade_points = compute_grade(pct)
        responses.append(
            StudentMarkResponse(
                id=m.id,
                course_id=m.course_id,
                course_code=m.course.code,
                course_title=m.course.title,
                component=m.component,
                score=float(m.score),
                max_score=float(m.max_score),
                percentage=pct,
                grade=grade,
                grade_points=grade_points,
                recorded_at=m.recorded_at,
            )
        )
    return responses


def get_marks_summary(db: Session, user_id: int) -> MarksSummaryResponse:
    """Return per-course mark breakdowns, safely handling incomplete marks."""
    profile = _require_student_profile(db, user_id)
    enrollments = (
        db.query(Enrollment)
        .filter(Enrollment.student_id == profile.id)
        .options(joinedload(Enrollment.course))
        .order_by(Enrollment.id.asc())
        .all()
    )
    if not enrollments:
        return MarksSummaryResponse(courses=[])

    course_ids = [e.course_id for e in enrollments]
    marks = (
        db.query(Mark)
        .filter(
            Mark.student_id == profile.id,
            Mark.course_id.in_(course_ids),
        )
        .order_by(Mark.course_id.asc(), Mark.component.asc())
        .all()
    )

    marks_by_course: dict[int, list[Mark]] = {cid: [] for cid in course_ids}
    for m in marks:
        marks_by_course[m.course_id].append(m)

    summaries = []
    for e in enrollments:
        c = e.course
        c_marks = marks_by_course.get(c.id, [])
        component_responses = []
        total_score = 0.0
        total_max = 0.0

        for m in c_marks:
            pct = round(float(m.score) * 100.0 / float(m.max_score), 2)
            grade, grade_points = compute_grade(pct)
            total_score += float(m.score)
            total_max += float(m.max_score)
            component_responses.append(
                StudentMarkResponse(
                    id=m.id,
                    course_id=m.course_id,
                    course_code=c.code,
                    course_title=c.title,
                    component=m.component,
                    score=float(m.score),
                    max_score=float(m.max_score),
                    percentage=pct,
                    grade=grade,
                    grade_points=grade_points,
                    recorded_at=m.recorded_at,
                )
            )

        # Mark completeness policy:
        # The schema lacks a course-level syllabus or component specification
        # defining which components a course requires, or guaranteeing any specific
        # total max points. Therefore, component completeness cannot be inferred
        # from marks alone.
        #
        # Instead, we distinguish course state using the student's enrollment lifecycle:
        # - If no marks exist -> overall is None, status = "No Marks".
        # - If the enrollment is ACTIVE -> the course is still ongoing; overall
        #   final grade and percentage are unavailable/incomplete (None) with
        #   status = "Incomplete", avoiding falsely claiming in-progress marks are final.
        # - If the enrollment is COMPLETED -> the course has concluded; the overall
        #   percentage is calculated from all recorded component marks across whatever
        #   scale was used, with status = "Complete".
        # - If the enrollment is DROPPED -> overall is None, status = "Dropped".
        if not c_marks:
            overall_pct = None
            overall_grade = None
            overall_pts = None
            c_status = "No Marks"
        elif e.status == EnrollmentStatus.COMPLETED and total_max > 0:
            overall_pct = round(total_score * 100.0 / total_max, 2)
            overall_grade, overall_pts = compute_grade(overall_pct)
            c_status = "Complete"
        elif e.status == EnrollmentStatus.DROPPED:
            overall_pct = None
            overall_grade = None
            overall_pts = None
            c_status = "Dropped"
        else:
            overall_pct = None
            overall_grade = None
            overall_pts = None
            c_status = "Incomplete"

        summaries.append(
            CourseMarkSummary(
                course_id=c.id,
                course_code=c.code,
                course_title=c.title,
                credits=c.credits,
                components=component_responses,
                overall_percentage=overall_pct,
                overall_grade=overall_grade,
                overall_grade_points=overall_pts,
                status=c_status,
            )
        )

    return MarksSummaryResponse(courses=summaries)


def list_announcements(
    db: Session, user_id: int
) -> list[StudentAnnouncementResponse]:
    """Return active published notices targeted to all students."""
    _require_student_profile(db, user_id)
    announcements = (
        db.query(Announcement)
        .filter(
            Announcement.status == AnnouncementStatus.ACTIVE,
            Announcement.audience.in_(
                [AnnouncementAudience.ALL, AnnouncementAudience.STUDENTS]
            ),
            Announcement.published_at.is_not(None),
        )
        .order_by(Announcement.published_at.desc(), Announcement.id.desc())
        .all()
    )
    return [
        StudentAnnouncementResponse(
            id=a.id,
            title=a.title,
            body=a.body,
            category=a.category,
            audience=a.audience,
            published_at=a.published_at,
        )
        for a in announcements
    ]


def get_dashboard(db: Session, user_id: int) -> DashboardResponse:
    """Return composite metrics for the student dashboard avoiding N+1 queries."""
    profile = _require_student_profile(db, user_id)

    # 1. Active enrollments and credits
    active_enrollments = (
        db.query(Enrollment)
        .filter(
            Enrollment.student_id == profile.id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
        .options(joinedload(Enrollment.course))
        .all()
    )
    enrolled_courses_count = len(active_enrollments)
    total_credits = sum(e.course.credits for e in active_enrollments)
    active_course_ids = [e.course_id for e in active_enrollments]

    # 2. Overall attendance rate
    att_row = (
        db.query(
            func.count(AttendanceRecord.id).label("total"),
            func.count(
                case((AttendanceRecord.status.in_(ATTENDED_STATUSES), 1))
            ).label("attended"),
        )
        .filter(AttendanceRecord.student_id == profile.id)
        .first()
    )
    if att_row and att_row.total > 0:
        overall_att_pct = round(att_row.attended * 100.0 / att_row.total, 2)
    else:
        overall_att_pct = None

    # 3. Upcoming assignments (up to 5 future-due active assignments)
    upcoming_assignments = []
    if active_course_ids:
        now = datetime.now(UTC)
        assignment_rows = (
            db.query(Assignment, AssignmentSubmission, Course)
            .join(Course, Course.id == Assignment.course_id)
            .outerjoin(
                AssignmentSubmission,
                (AssignmentSubmission.assignment_id == Assignment.id)
                & (AssignmentSubmission.student_id == profile.id),
            )
            .filter(
                Assignment.course_id.in_(active_course_ids),
                Assignment.is_active.is_(True),
                Assignment.due_at.is_not(None),
                Assignment.due_at >= now,
            )
            .order_by(Assignment.due_at.asc(), Assignment.id.asc())
            .limit(5)
            .all()
        )
        for assignment, submission, course in assignment_rows:
            upcoming_assignments.append(
                StudentAssignmentResponse(
                    id=assignment.id,
                    course_id=assignment.course_id,
                    course_code=course.code,
                    course_title=course.title,
                    title=assignment.title,
                    description=assignment.description,
                    due_at=assignment.due_at,
                    weightage_percent=(
                        float(assignment.weightage_percent)
                        if assignment.weightage_percent is not None
                        else None
                    ),
                    max_score=float(assignment.max_score),
                    is_active=assignment.is_active,
                    submission_id=submission.id if submission else None,
                    submission_status=submission.status if submission else None,
                    submitted_at=submission.submitted_at if submission else None,
                    content=submission.content if submission else None,
                    score=(
                        float(submission.score)
                        if submission and submission.score is not None
                        else None
                    ),
                    feedback=submission.feedback if submission else None,
                    graded_at=submission.graded_at if submission else None,
                )
            )

    # 4. Recent announcements (up to 3)
    announcement_rows = (
        db.query(Announcement)
        .filter(
            Announcement.status == AnnouncementStatus.ACTIVE,
            Announcement.audience.in_(
                [AnnouncementAudience.ALL, AnnouncementAudience.STUDENTS]
            ),
            Announcement.published_at.is_not(None),
        )
        .order_by(Announcement.published_at.desc(), Announcement.id.desc())
        .limit(3)
        .all()
    )
    recent_announcements = [
        StudentAnnouncementResponse(
            id=a.id,
            title=a.title,
            body=a.body,
            category=a.category,
            audience=a.audience,
            published_at=a.published_at,
        )
        for a in announcement_rows
    ]

    return DashboardResponse(
        enrolled_courses_count=enrolled_courses_count,
        total_credits=total_credits,
        overall_attendance_percent=overall_att_pct,
        upcoming_assignments=upcoming_assignments,
        recent_announcements=recent_announcements,
    )
