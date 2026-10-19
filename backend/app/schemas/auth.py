"""
Authentication request/response schemas (schemas/auth.py).

Pydantic models define the SHAPE of data crossing the API boundary. They are
deliberately separate from the SQLAlchemy models: an API must never accept or
emit raw ORM objects, because that would expose columns like ``password_hash``
the moment the model grows a new field.

``UserResponse`` is a "safe projection" - it lists, one by one, exactly the
fields a client may see. There is no way to accidentally leak the password
hash through it, because the hash is simply not among its fields.
"""

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import UserRole


class LoginRequest(BaseModel):
    """Body of ``POST /api/v1/auth/login``.

    ``EmailStr`` (pydantic-email-validator) both type-checks and normalises the
    address. Note the DATABASE still enforces ``email_normalized = lower(email)``
    as defense in depth - we do not rely on this schema alone.
    """

    email: EmailStr
    #: Maximum length matches the String(255) column, so oversized input fails
    #: with a clean 422 instead of a database error.
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    """The ONLY user shape returned by the API. Never includes password_hash."""

    id: int
    email: str
    role: UserRole

    model_config = {"from_attributes": True}
