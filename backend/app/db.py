from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Ensure the pgvector extension and tables exist."""
    from app.models import Base

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def get_session():
    """FastAPI dependency yielding a scoped DB session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
