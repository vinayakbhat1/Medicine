import re
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _tz(v: str | None):
    if v is None:
        return v
    try:
        ZoneInfo(v)
    except Exception:
        raise ValueError("Unknown IANA timezone, e.g. Asia/Kolkata")
    return v


class OtpRequest(BaseModel):
    identifier: str = Field(min_length=5, max_length=160, description="Mobile number or email")


class OtpVerify(BaseModel):
    identifier: str = Field(min_length=5, max_length=160)
    code: str = Field(min_length=6, max_length=6)
    name: str | None = Field(default=None, max_length=80)


class FamilyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    timezone: str = "Asia/Kolkata"
    creator_name: str | None = Field(default=None, max_length=80)
    @field_validator("timezone")
    @classmethod
    def _tzv(cls, v):
        return _tz(v)


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    relationship: str = Field(default="Other", max_length=40)
    dob: date | None = None  # optional – collect minimum data
    is_dependent: bool = True
    invite_identifier: str | None = Field(default=None, max_length=160)


class ConsentUpdate(BaseModel):
    share_notes: bool


class MedicineCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    strength: str = Field(default="", max_length=40)
    dose: str = Field(min_length=1, max_length=60)
    route: str | None = Field(default=None, max_length=40)
    instructions: str | None = Field(default=None, max_length=500)
    low_stock_threshold: int = Field(default=0, ge=0, le=10000)
    initial_stock: int | None = Field(default=None, ge=0, le=100000)


class MedicinePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    strength: str | None = Field(default=None, max_length=40)
    dose: str | None = Field(default=None, min_length=1, max_length=60)
    route: str | None = Field(default=None, max_length=40)
    instructions: str | None = Field(default=None, max_length=500)
    low_stock_threshold: int | None = Field(default=None, ge=0, le=10000)
    status: Literal["active", "paused", "discontinued"] | None = None


class ScheduleCreate(BaseModel):
    times: list[str] = Field(min_length=1, max_length=6)
    days_of_week: list[int] = Field(default_factory=list)
    start_date: date
    end_date: date | None = None
    timezone: str | None = None
    @field_validator("timezone")
    @classmethod
    def _tzv(cls, v):
        return _tz(v)

    @field_validator("times")
    @classmethod
    def _times(cls, v):
        if any(not TIME_RE.match(t) for t in v):
            raise ValueError("Times must be HH:MM (24h)")
        if len(set(v)) != len(v):
            raise ValueError("Duplicate times")
        return sorted(v)

    @field_validator("days_of_week")
    @classmethod
    def _days(cls, v):
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days_of_week must be 0 (Mon) to 6 (Sun)")
        return sorted(set(v))


class DoseAction(BaseModel):
    action: Literal["taken", "skipped", "no_stock"]
    note: str | None = Field(default=None, max_length=300)


class StockAdjust(BaseModel):
    delta: int = Field(ge=-10000, le=10000)
    reason: str = Field(default="manual_adjustment", max_length=60)

    @field_validator("delta")
    @classmethod
    def _nz(cls, v):
        if v == 0:
            raise ValueError("delta cannot be 0")
        return v
