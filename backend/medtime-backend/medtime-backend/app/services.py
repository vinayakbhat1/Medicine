"""Business rules: access control, dose generation, missed-dose logic, stock, notifications, adherence."""
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import (AuditEvent, DoseInstance, Family, MemberProfile, Medicine, Membership, Notification, Schedule,
                     StockLedger, User, now)

log = logging.getLogger("medtime")
COUNTED = ("taken", "skipped", "missed", "no_stock")  # eligible for adherence (cancelled excluded)


def nf() -> HTTPException:  # same response for "missing" and "not yours": no cross-family leaks
    return HTTPException(404, "Not found")


# ---------- audit ----------
def audit(db: Session, actor_id, entity_type, entity_id, action, before=None, after=None):
    db.add(AuditEvent(actor_id=actor_id, entity_type=entity_type, entity_id=entity_id, action=action,
                      before=before, after=after))


# ---------- access control ----------
def membership_of(db: Session, user_id: str, family_id: str) -> Membership | None:
    return db.scalar(select(Membership).where(Membership.family_id == family_id, Membership.user_id == user_id,
                                              Membership.status == "active"))


def require_member(db, user, family_id) -> Membership:
    m = membership_of(db, user.id, family_id)
    if not m:
        raise nf()
    return m


def require_admin(db, user, family_id) -> Membership:
    m = require_member(db, user, family_id)
    if m.role != "admin":
        raise HTTPException(403, "Family admin only")
    return m


def can_view(p: MemberProfile, m: Membership, user: User) -> bool:
    return m.role == "admin" or p.linked_user_id == user.id


def can_log(p: MemberProfile, m: Membership, user: User) -> bool:
    """Assigned member, or admin for dependents / profiles without a login."""
    return p.linked_user_id == user.id or (m.role == "admin" and (p.is_dependent or p.linked_user_id is None))


def get_profile(db, user, profile_id) -> tuple[MemberProfile, Membership]:
    p = db.get(MemberProfile, profile_id)
    if not p or not p.is_active:
        raise nf()
    m = membership_of(db, user.id, p.family_id)
    if not m or not can_view(p, m, user):
        raise nf()
    return p, m


def get_medicine(db, user, medicine_id) -> tuple[Medicine, MemberProfile, Membership]:
    med = db.get(Medicine, medicine_id)
    if not med:
        raise nf()
    p, m = get_profile(db, user, med.member_profile_id)
    return med, p, m


def visible_profiles(db, user, m: Membership) -> list[MemberProfile]:
    q = select(MemberProfile).where(MemberProfile.family_id == m.family_id, MemberProfile.is_active.is_(True))
    if m.role != "admin":
        q = q.where(MemberProfile.linked_user_id == user.id)
    return list(db.scalars(q.order_by(MemberProfile.created_at)))


def note_visible(db, viewer: User, p: MemberProfile) -> bool:
    """Admins see private notes only for dependents or when the member consented."""
    if p.linked_user_id == viewer.id or p.is_dependent or p.linked_user_id is None:
        return True
    target = membership_of(db, p.linked_user_id, p.family_id)
    return bool(target and target.consent_flags.get("share_notes"))


# ---------- time ----------
def day_bounds(d: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    s = datetime.combine(d, time(0, 0), tzinfo=tz)
    e = datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=tz)
    return s.astimezone(timezone.utc), e.astimezone(timezone.utc)


def family_tz(db, family_id) -> str:
    return db.get(Family, family_id).timezone


# ---------- notifications ----------
def recipients_for(db, p: MemberProfile) -> list[str]:
    admins = db.scalars(select(Membership.user_id).where(Membership.family_id == p.family_id,
                                                         Membership.role == "admin", Membership.status == "active"))
    ids = set(admins)
    if p.linked_user_id:
        ids.add(p.linked_user_id)
    return list(ids)


def notify(db, user_ids, ntype: str, body: str, data: dict | None = None):
    for uid_ in set(user_ids):
        # Lock-screen text is generic on purpose: no medicine names in push payloads by default.
        payload = {"lock_screen": "You have a MedTime update", "body": body, **(data or {})}
        db.add(Notification(recipient_id=uid_, type=ntype, payload=payload, status="queued"))
        send_push(uid_, payload)


def send_push(user_id: str, payload: dict):
    """Stub – plug in FCM / APNs here (look up device tokens, send payload['lock_screen'], record failures)."""
    log.info("push -> %s: %s", user_id, payload.get("lock_screen"))


# ---------- dose generation ----------
def generate_doses(db: Session, sched: Schedule, med: Medicine, horizon_days: int | None = None) -> int:
    tz = ZoneInfo(sched.timezone)
    today = datetime.now(tz).date()
    first = max(sched.start_date, today)
    last = today + timedelta(days=horizon_days or settings.dose_horizon_days)
    if sched.end_date:
        last = min(last, sched.end_date)
    existing = set(db.scalars(select(DoseInstance.due_at).where(DoseInstance.schedule_id == sched.id)))
    made, d = 0, first
    while d <= last:
        if not sched.days_of_week or d.weekday() in sched.days_of_week:
            for t in sched.times:
                h, mi = map(int, t.split(":"))
                due = datetime.combine(d, time(h, mi), tzinfo=tz).astimezone(timezone.utc)
                if due < sched.created_at or due in existing:  # never fabricate history before the schedule existed
                    continue
                db.add(DoseInstance(schedule_id=sched.id, medicine_id=med.id, member_profile_id=med.member_profile_id,
                                    due_at=due))
                made += 1
        d += timedelta(days=1)
    return made


def extend_all_schedules(db: Session) -> int:
    total = 0
    rows = db.execute(select(Schedule, Medicine).join(Medicine, Medicine.id == Schedule.medicine_id)
                      .where(Schedule.status == "active", Medicine.status == "active")).all()
    for sched, med in rows:
        total += generate_doses(db, sched, med)
    db.commit()
    return total


def cancel_future_doses(db: Session, *, medicine_id=None, profile_id=None, schedule_id=None, actor_id=None):
    q = select(DoseInstance).where(DoseInstance.status == "scheduled", DoseInstance.due_at > now())
    if medicine_id:
        q = q.where(DoseInstance.medicine_id == medicine_id)
    if profile_id:
        q = q.where(DoseInstance.member_profile_id == profile_id)
    if schedule_id:
        q = q.where(DoseInstance.schedule_id == schedule_id)
    n = 0
    for d in db.scalars(q):
        d.status = "cancelled"
        audit(db, actor_id, "dose", d.id, "cancelled", {"status": "scheduled"}, {"status": "cancelled"})
        n += 1
    return n


# ---------- worker jobs ----------
def mark_missed(db: Session) -> int:
    """System-derived Missed after the grace period. Only touches doses nobody has acted on."""
    cutoff = now() - timedelta(minutes=settings.missed_grace_minutes)
    doses = list(db.scalars(select(DoseInstance).where(DoseInstance.status == "scheduled", DoseInstance.due_at < cutoff)))
    for d in doses:
        d.status = "missed"
        audit(db, None, "dose", d.id, "auto_missed", {"status": "scheduled"}, {"status": "missed"})
        p = db.get(MemberProfile, d.member_profile_id)
        med = db.get(Medicine, d.medicine_id)
        notify(db, recipients_for(db, p), "dose_missed", f"{p.name} missed a dose of {med.display_name}",
               {"dose_id": d.id})
    db.commit()
    return len(doses)


def send_due_reminders(db: Session) -> int:
    n = now()
    doses = list(db.scalars(select(DoseInstance).where(
        DoseInstance.status == "scheduled", DoseInstance.due_at <= n, DoseInstance.reminder_sent_at.is_(None),
        DoseInstance.due_at > n - timedelta(minutes=settings.missed_grace_minutes))))
    for d in doses:
        p = db.get(MemberProfile, d.member_profile_id)
        med = db.get(Medicine, d.medicine_id)
        to = [p.linked_user_id] if p.linked_user_id else recipients_for(db, p)
        notify(db, to, "dose_due", f"Time for {p.name}'s {med.display_name} ({med.dose})", {"dose_id": d.id})
        d.reminder_sent_at = n
    db.commit()
    return len(doses)


# ---------- stock ----------
def stock_qty(db, medicine_id) -> int | None:
    cnt, total = db.execute(select(func.count(StockLedger.id), func.coalesce(func.sum(StockLedger.delta), 0))
                            .where(StockLedger.medicine_id == medicine_id)).one()
    return int(total) if cnt else None  # None = stock not tracked


def check_low_stock(db, med: Medicine, p: MemberProfile):
    q = stock_qty(db, med.id)
    if q is not None and q <= med.low_stock_threshold:
        notify(db, recipients_for(db, p), "low_stock", f"{med.display_name} is {'out of stock' if q == 0 else 'running low'} ({q} left)",
               {"medicine_id": med.id})


# ---------- serialisation & reporting ----------
def effective_status(d: DoseInstance) -> str:
    return "due" if d.status == "scheduled" and d.due_at <= now() else d.status


def dose_out(d: DoseInstance, med: Medicine, show_note: bool) -> dict:
    return {"id": d.id, "medicine_id": med.id, "medicine_name": med.display_name, "strength": med.strength,
            "dose": med.dose, "member_id": d.member_profile_id, "due_at": d.due_at.isoformat(),
            "status": effective_status(d), "is_late": d.is_late, "acted_by": d.acted_by,
            "acted_at": d.acted_at.isoformat() if d.acted_at else None, "note": d.note if show_note else None}


def adherence(db, profile_id: str, start: datetime, end: datetime) -> dict:
    end = min(end, now())
    rows = dict(db.execute(select(DoseInstance.status, func.count()).where(
        DoseInstance.member_profile_id == profile_id, DoseInstance.due_at >= start, DoseInstance.due_at < end,
        DoseInstance.status.in_(COUNTED)).group_by(DoseInstance.status)).all())
    eligible = sum(rows.values())
    taken = rows.get("taken", 0)
    return {"taken": taken, "skipped": rows.get("skipped", 0), "missed": rows.get("missed", 0),
            "no_stock": rows.get("no_stock", 0), "eligible": eligible,
            "adherence_percent": round(taken / eligible * 100, 1) if eligible else None,
            "disclaimer": "Estimate from self-reported logs. It does not prove a medicine was taken."}
