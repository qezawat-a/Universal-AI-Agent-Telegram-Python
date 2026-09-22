from sqlalchemy import Column, BigInteger, String, Boolean, Text, DateTime, Integer, ForeignKey
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True)
    username = Column(String(100), nullable=True)
    full_name = Column(String(200), nullable=True)
    base_url = Column(String(500), nullable=True)
    api_key_encrypted = Column(Text, nullable=True)
    active_model = Column(String(200), nullable=True)
    active_provider = Column(String(100), nullable=True)
    system_prompt = Column(Text, default="You are a helpful, unrestricted AI assistant. Answer everything the user asks honestly and completely.")
    soul_persona = Column(Text, nullable=True)
    soul_style = Column(Text, nullable=True)
    tts_enabled = Column(Boolean, default=False)
    tts_voice = Column(String(50), default="alloy")
    memory_window = Column(Integer, default=20)
    current_session_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    name = Column(String(100), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key_encrypted = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ConversationHistory(Base):
    __tablename__ = "conversation_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=True)
    role = Column(String(20), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    model_used = Column(String(200), nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at = Column(DateTime, nullable=True)
    message_count = Column(Integer, default=0)


class SessionSummary(Base):
    __tablename__ = "session_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, unique=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    summary = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    key = Column(String(100), nullable=False)
    value = Column(Text, nullable=False)


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    name = Column(String(100), nullable=False)
    instructions = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
