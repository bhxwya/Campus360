"""Activation request/response schemas (schemas/activation.py).

The public activation endpoint lets a student who was imported with an
inactive account choose their own permanent password. It is intentionally
unauthenticated - the activation token proves identity - and every failure
returns a generic message so the endpoint cannot be used to enumerate
whether an arbitrary email has an account.
"""

from pydantic import BaseModel, Field


class ActivateRequest(BaseModel):
    """Body of ``POST /auth/activate``.

    ``token`` is the single-use activation link token delivered by email.
    ``password`` is the student's chosen permanent password.
    """

    token: str = Field(min_length=1)
    password: str = Field(min_length=1, max_length=128)


class ActivateResponse(BaseModel):
    """Successful activation: the account is now active and usable."""

    message: str
    email: str
