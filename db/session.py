import logging
import os
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from db.models import Base

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

# fallback to SQLite for local/Termux use
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./agent.db"
    print("⚠️  No DATABASE_URL found — using SQLite (local mode)")

# Railway Postgres fix: replace postgres:// with postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db():
    # 1) create tables that don't exist yet
    Base.metadata.create_all(bind=engine)
    # 2) add any missing columns to tables that already exist (e.g. an older
    #    Postgres 'users' table lacking base_url / api_key_encrypted / tts_enabled / ...)
    #    Hardcoded + idempotent (ADD COLUMN IF NOT EXISTS) so it can't fail on a
    #    column that's already there, and doesn't depend on schema introspection.
    _sync_missing_columns()
    _ensure_sessions_table()


def _sync_missing_columns():
    # SQLite has no ADD COLUMN IF NOT EXISTS -> inspect + add only missing.
    # Works on Postgres too.
    wanted = {
        "username": "VARCHAR(100)",
        "full_name": "VARCHAR(200)",
        "base_url": "VARCHAR(500)",
        "api_key_encrypted": "TEXT",
        "active_model": "VARCHAR(200)",
        "active_provider": "VARCHAR(100)",
        "system_prompt": "TEXT",
        "soul_persona": "TEXT",
        "soul_style": "TEXT",
        "tts_enabled": "BOOLEAN",
        "tts_voice": "VARCHAR(50)",
        "memory_window": "INTEGER",
        "current_session_id": "INTEGER",
        "created_at": "TIMESTAMP",
    }
    try:
        insp = inspect(engine)
        existing = {c["name"] for c in insp.get_columns("users")} if insp.has_table("users") else set()
    except Exception:
        existing = set()
    missing = [(k, v) for k, v in wanted.items() if k not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for col, typ in missing:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typ}"))
            except Exception as e:
                logger.warning("add column %s failed (may exist): %s", col, e)
    logger.info("init_db: synced users columns (added %s)", [c for c, _ in missing])


def _ensure_sessions_table():
    """Recreate the sessions table if it predates the current model schema
    (e.g. an old table keyed by user_id instead of telegram_id). Patching
    columns one-by-one can't fix a structural mismatch, so drop+recreate it when
    stale. Once the table matches the model this is a no-op (survives restarts
    without wiping conversation sessions)."""
    from db.models import Session

    inspector = inspect(engine)
    if not inspector.has_table("sessions"):
        Base.metadata.create_all(bind=engine, tables=[Session.__table__])
        return
    cols = {c["name"] for c in inspector.get_columns("sessions")}
    if "telegram_id" not in cols or "user_id" in cols:
        logger.warning("sessions table schema is stale (user_id-based); recreating it")
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS sessions CASCADE"))
        Base.metadata.create_all(bind=engine, tables=[Session.__table__])
        logger.info("sessions table recreated from current model")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
