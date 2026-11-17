"""SQLite persistence and a small, explicit schema migration."""
import os
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class StreamDraft(Base):
    __tablename__ = "stream_drafts"
    stream_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # The two snapshots make local edits reversible and enable conflict checks.
    baseline: Mapped[dict] = mapped_column(JSON)
    draft: Mapped[dict] = mapped_column(JSON)
    dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)


def make_engine(url=None):
    url = url or os.getenv("DATABASE_URL", "sqlite:///./adrobot.db")
    kwargs = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=kwargs)


engine = make_engine()
Session = sessionmaker(bind=engine, expire_on_commit=False)


def migrate(target_engine=engine):
    """Migration 1 creates the initial schema; safe to run more than once."""
    Base.metadata.create_all(target_engine)
    with sessionmaker(bind=target_engine)() as db:
        if db.scalar(select(SchemaVersion).where(SchemaVersion.version == 1)) is None:
            db.add(SchemaVersion(version=1))
            db.commit()


if __name__ == "__main__":
    migrate()
    print("Database migration 1 applied.")
