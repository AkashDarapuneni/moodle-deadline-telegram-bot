import os
import ssl
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://user:password@localhost:3306/moodle_bot",
)

url = make_url(DATABASE_URL)

if url.drivername == "mysql":
    url = url.set(drivername="mysql+pymysql")

# Configure secure SSL context for TiDB Cloud
# Force secure SSL context for TiDB Cloud
connect_args = {"ssl": {}}

engine = create_engine(
    url, 
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=connect_args
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    moodle_url: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    calendar_link: Mapped[str] = mapped_column(String(500), nullable=True)
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
    assignment_title: Mapped[str] = mapped_column(String(255), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_24h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_6h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_1h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# Automatically create all tables in TiDB Cloud if they do not exist
Base.metadata.create_all(bind=engine)
