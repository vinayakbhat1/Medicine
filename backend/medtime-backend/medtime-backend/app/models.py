import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, always returns timezone-aware UTC (works on SQLite and PostgreSQL)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, v, dialect):
        if v is None:
            return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, v, dialect):
        return v.replace(tzinfo=timezone.utc) if v else None


def pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=uid)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(32), unique=True)
    email: Mapped[str | None] = mapped_column(String(160), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class OtpCode(Base):
    __tablename__ = "otp_codes"
    id: Mapped[str] = pk()
    identifier: Mapped[str] = mapped_column(String(160), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Family(Base):
    __tablename__ = "families"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(36))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Membership(Base):
    __tablename__ = "family_memberships"
    __table_args__ = (UniqueConstraint("family_id", "user_id"),)
    id: Mapped[str] = pk()
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | removed
    consent_flags: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class MemberProfile(Base):
    """A person whose medicines are tracked. Dependents have no login."""

    __tablename__ = "member_profiles"
    id: Mapped[str] = pk()
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    linked_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    relationship: Mapped[str] = mapped_column(String(40), default="Other")
    dob: Mapped[date | None] = mapped_column(Date)
    is_dependent: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = pk()
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("member_profiles.id"))
    identifier: Mapped[str] = mapped_column(String(160))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_by: Mapped[str] = mapped_column(String(36))


class Medicine(Base):
    __tablename__ = "medicines"
    id: Mapped[str] = pk()
    member_profile_id: Mapped[str] = mapped_column(ForeignKey("member_profiles.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    strength: Mapped[str] = mapped_column(String(40), default="")
    dose: Mapped[str] = mapped_column(String(60))  # e.g. "1 tablet" (user-entered, never inferred)
    route: Mapped[str | None] = mapped_column(String(40))
    instructions: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | paused | discontinued
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = pk()
    medicine_id: Mapped[str] = mapped_column(ForeignKey("medicines.id"), index=True)
    times: Mapped[list] = mapped_column(JSON)  # ["09:00", "21:00"] local times
    days_of_week: Mapped[list] = mapped_column(JSON, default=list)  # 0=Mon..6=Sun; empty = every day
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class DoseInstance(Base):
    __tablename__ = "dose_instances"
    __table_args__ = (UniqueConstraint("schedule_id", "due_at"), Index("ix_dose_member_due", "member_profile_id", "due_at"))
    id: Mapped[str] = pk()
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id"))
    medicine_id: Mapped[str] = mapped_column(ForeignKey("medicines.id"), index=True)
    member_profile_id: Mapped[str] = mapped_column(ForeignKey("member_profiles.id"))
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    # scheduled | taken | skipped | missed | no_stock | cancelled   ("due" is derived on read)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    acted_by: Mapped[str | None] = mapped_column(String(36))
    acted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(300))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class StockLedger(Base):
    __tablename__ = "stock_ledger"
    id: Mapped[str] = pk()
    medicine_id: Mapped[str] = mapped_column(ForeignKey("medicines.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(60))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = pk()
    recipient_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = pk()
    actor_id: Mapped[str | None] = mapped_column(String(36))  # None = system
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(40))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(String(36))
    key: Mapped[str] = mapped_column(String(100))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
