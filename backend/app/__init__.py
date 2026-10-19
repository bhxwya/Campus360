"""Campus360 backend package (backend/app).

Layout:
  app/core/   configuration and cross-cutting utilities (settings, security)
  app/db/     SQLAlchemy engine, session handling, declarative base
  app/api/    FastAPI routers, one module per resource/feature
  app/models/ SQLAlchemy models (Stage 2)
  app/schemas/ Pydantic request/response schemas (Stage 2+)
  app/services/ business logic used by routes (Stage 4)
"""
