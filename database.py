import os
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://user:password@localhost:3306/moodle_bot",
)

url = make_url(DATABASE_URL)

# Ensure the correct PyMySQL driver is used
if url.drivername == "mysql":
    url = url.set(drivername="mysql+pymysql")

# pool_recycle=300 is essential for MySQL to prevent "MySQL server has gone away" errors
engine = create_engine(
    url, 
    pool_pre_ping=True,
    pool_recycle=300
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # MySQL requires a character limit for String columns
    moodle_url: Mapped[str] = mapped_column(String(255), nullable=False)
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
    # MySQL requires a character limit for String columns
    assignment_title: Mapped[str] = mapped_column(String(255), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_24h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_6h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_1h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
