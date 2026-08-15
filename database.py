import os
import ssl
import certifi
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./lms_bot.db"
)

# Convert URL formats for Render & TiDB compatibility
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("mysql://"):
    DATABASE_URL = DATABASE_URL.replace("mysql://", "mysql+pymysql://", 1)

url = make_url(DATABASE_URL)

# The BULLETPROOF SSL Armor for TiDB Serverless
connect_args = {}
if "tidbcloud.com" in DATABASE_URL or "mysql" in url.drivername:
    connect_args["ssl"] = ssl.create_default_context(cafile=certifi.where())

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
    __tablename__ = "lms_users"  # <--- RENAMED TO CREATE FRESH TABLE
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    moodle_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    streak_count: Mapped[int] = mapped_column(BigInteger, default=0)
    rank: Mapped[str] = mapped_column(String(100), default="Rookie 🔰")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

class Deadline(Base):
    __tablename__ = "lms_deadlines"  # <--- RENAMED TO CREATE FRESH TABLE
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("lms_users.telegram_chat_id", ondelete="CASCADE"), # <--- UPDATED LINK
        nullable=False,
    )
    assignment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    assignment_title: Mapped[str] = mapped_column(String(500), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Premium AI Features
    difficulty: Mapped[str] = mapped_column(String(100), default="⚪ Unknown")
    ai_tip: Mapped[str] = mapped_column(String(1000), default="Just get it done!")

    # Alert Flags
    sent_24h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_6h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_2h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_1h_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_50m_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

Base.metadata.create_all(bind=engine)
