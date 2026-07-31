import os
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/moodle_bot",
)

# Ensure the URL uses psycopg2 and handles Render SSL requirements
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# Pass connect_args to explicitly handle SSL requirement for Render
connect_args = {}
if "render.com" in DATABASE_URL:
    connect_args["sslmode"] = "require"

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    moodle_url: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Deadline(Base):
    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    assignment_title: Mapped[str] = mapped_column(String, nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_24h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_6h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_1h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
