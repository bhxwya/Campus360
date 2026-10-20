"""
Admin API tests for announcements / notices
(backend/tests/test_admin_announcements.py).

Covers the notice lifecycle: publish, list/filter, read, edit, archive and
re-publish.

Two design decisions are pinned here:
  * NOTICES ARE ARCHIVED, NEVER DELETED - the row survives with its author
    link intact, so history is preserved.
  * ``created_by`` COMES FROM THE SESSION - a client cannot forge
    authorship by putting somebody else's id in the body.
"""

from app.db.session import SessionLocal
from app.models import Announcement


def _notice(admin_client, **overrides) -> dict:
    """POST a notice and return the parsed body (asserting 201)."""
    payload = {
        "title": "Mid-Term Examination Schedule",
        "body": "The mid-term examinations begin on the 15th.",
        "category": "examination",
        "audience": "all",
    }
    payload.update(overrides)
    response = admin_client.client.post(
        "/api/v1/admin/announcements", json=payload, headers=admin_client.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_notice_returns_201(admin_client, db_cleaner) -> None:
    """Publishing a notice returns the safe projection."""
    body = _notice(admin_client)

    assert body["title"] == "Mid-Term Examination Schedule"
    assert body["category"] == "examination"
    assert body["audience"] == "all"
    assert body["status"] == "active"
    assert body["published_at"] is not None


def test_notice_author_is_the_authenticated_admin(admin_client, db_cleaner) -> None:
    """``created_by`` comes from the SESSION, never from the body.

    The body schema has no ``created_by`` field at all, so a client cannot
    forge authorship - and the recorded author is the logged-in admin.
    """
    body = _notice(admin_client)

    assert body["created_by"] == admin_client.user["id"]
    assert body["author_email"] == admin_client.user["email"]


def test_notice_body_cannot_forge_the_author(admin_client, db_cleaner) -> None:
    """Sending ``created_by`` in the body is ignored (extra fields)."""
    response = admin_client.client.post(
        "/api/v1/admin/announcements",
        json={
            "title": "Forged",
            "body": "Someone else's notice.",
            "category": "administrative",
            "created_by": 99999999,
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == admin_client.user["id"]


def test_notice_with_invalid_category_returns_422(admin_client, db_cleaner) -> None:
    """An unknown category is rejected at the edge with 422."""
    response = admin_client.client.post(
        "/api/v1/admin/announcements",
        json={"title": "Bad", "body": "Bad", "category": "not_a_category"},
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text


def test_draft_notice_has_no_published_at(admin_client, db_cleaner) -> None:
    """A DRAFT has no publication instant until it is published."""
    body = _notice(admin_client, status="archived")

    assert body["status"] == "archived"
    assert body["published_at"] is None


def test_list_notices_is_paginated_and_filterable(admin_client, db_cleaner) -> None:
    """The notice list reports the total and filters by status."""
    _notice(admin_client)
    archived = _notice(
        admin_client,
        title="Archived Notice",
        body="Old news.",
        status="archived",
    )

    response = admin_client.client.get("/api/v1/admin/announcements")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 2
    assert body["limit"] == 20

    only_active = admin_client.client.get(
        "/api/v1/admin/announcements", params={"status": "active"}
    )
    assert only_active.status_code == 200, only_active.text
    active_ids = {item["id"] for item in only_active.json()["items"]}
    assert archived["id"] not in active_ids


def test_get_notice_by_id(admin_client, db_cleaner) -> None:
    """One notice can be fetched by id."""
    created = _notice(admin_client)

    response = admin_client.client.get(f"/api/v1/admin/announcements/{created['id']}")

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Mid-Term Examination Schedule"


def test_get_unknown_notice_returns_404(admin_client, db_cleaner) -> None:
    """A missing notice is a clean 404."""
    response = admin_client.client.get("/api/v1/admin/announcements/99999999")

    assert response.status_code == 404, response.text


def test_patch_notice_updates_content(admin_client, db_cleaner) -> None:
    """Editing a notice changes its content, not its author."""
    created = _notice(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/announcements/{created['id']}",
        json={"title": "Updated Schedule", "body": "Now on the 20th."},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Updated Schedule"
    assert body["body"] == "Now on the 20th."
    # The author is unchanged by an edit.
    assert body["created_by"] == admin_client.user["id"]


def test_archive_notice_keeps_the_row(admin_client, db_cleaner) -> None:
    """Archiving HIDES a notice; it never deletes it."""
    created = _notice(admin_client)

    response = admin_client.client.post(
        f"/api/v1/admin/announcements/{created['id']}/archive", headers=admin_client.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"

    db = SessionLocal()
    try:
        # The ROW SURVIVES, with its author link intact.
        row = db.get(Announcement, created["id"])
        assert row is not None
        assert row.status.value == "archived"
        assert row.created_by == admin_client.user["id"]
    finally:
        db.close()


def test_publish_ann_restores_an_archived_notice(admin_client, db_cleaner) -> None:
    """A notice can be brought back from the archive."""
    created = _notice(admin_client)
    admin_client.client.post(
        f"/api/v1/admin/announcements/{created['id']}/archive", headers=admin_client.headers
    )

    response = admin_client.client.post(
        f"/api/v1/admin/announcements/{created['id']}/publish", headers=admin_client.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_publish_stamps_published_at_when_missing(admin_client, db_cleaner) -> None:
    """Publishing a draft sets the publication instant."""
    created = _notice(admin_client, status="archived")
    assert created["published_at"] is None

    response = admin_client.client.post(
        f"/api/v1/admin/announcements/{created['id']}/publish", headers=admin_client.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["published_at"] is not None


def test_archive_unknown_notice_returns_404(admin_client, db_cleaner) -> None:
    """Archiving a missing notice is a 404."""
    response = admin_client.client.post(
        "/api/v1/admin/announcements/99999999/archive", headers=admin_client.headers
    )

    assert response.status_code == 404, response.text
