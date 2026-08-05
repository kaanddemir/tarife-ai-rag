from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Ensure the pgvector extension and tables exist.

    This small catalog is loaded via full refresh, so there is no separate migration
    service; on a schema change the database is rebuilt from scratch (see
    reset_packages_table, called by the ingest).
    """
    from app.models import Base

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def reset_packages_table() -> None:
    """Drop and recreate the packages table so a schema change fully propagates.

    create_all only creates missing tables -- it never adds or drops columns on an
    existing one -- so after a model change the live table would keep stale columns
    and lack new ones. The catalog is a full refresh from JSON, so dropping it loses
    nothing; message_usage (quota data) is left untouched.
    """
    from app.models import Base, Package

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Package.__table__.drop(engine, checkfirst=True)
    Base.metadata.create_all(engine)


def get_session():
    """FastAPI dependency yielding a scoped database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
