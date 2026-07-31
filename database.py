import os
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/moodle_bot",
)

# Use SQLAlchemy's robust URL parser to cleanly handle credentials, drivers, and query params
url = make_url(DATABASE_URL)

if url.drivername.startswith("postgres") and not url.drivername.endswith("+psycopg2"):
    url = url.set(drivername="postgresql+psycopg2")

# Ensure sslmode is set for Render if not already specified
connect_args = url.query.copy()
if url.host and "render.com" in url.host and "sslmode" not in connect_args:
    connect_args["sslmode"] = "require"

# ADDED pool_recycle=300 to drop stale connections before Render kills them
engine = create_engine(
    url, 
    connect_args=connect_args, 
    pool_pre_ping=True,
    pool_recycle=300
)
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
