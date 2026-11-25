from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import DoseInstance, IdempotencyKey, MemberProfile, Medicine, StockLedger, User, now
from ..schemas import DoseAction
from ..security import current_user
from ..services import (adherence, audit, can_log, can_view, check_low_stock, day_bounds, dose_out, family_tz,
                        get_profile, membership_of, nf, note_visible, notify, recipients_for, stock_qty)

router = APIRouter(tags=["doses"])
FINAL = ("taken", "skipped", "no_stock")


@router.get("/members/{member_id}/doses")
def list_doses(member_id: str, from_: date | None = Query(default=None, alias="from"), to: date | None = None,
               limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_profile(db, user, member_id)
    tz = family_tz(db, p.family_id)
    today = datetime.now(ZoneInfo(tz)).date()
    start, _ = day_bounds(from_ or today, tz)
    _, end = day_bounds(to or from_ or today, tz)
    rows = db.execute(select(DoseInstance, Medicine).join(Medicine, Medicine.id == DoseInstance.medicine_id)
                      .where(DoseInstance.member_profile_id == p.id, DoseInstance.due_at >= start,
                             DoseInstance.due_at < end, DoseInstance.status != "cancelled")
                      .order_by(DoseInstance.due_at).limit(limit).offset(offset)).all()
    show = note_visible(db, user, p)
    return {"items": [dose_out(d, m, show) for d, m in rows], "limit": limit, "offset": offset}


@router.get("/members/{member_id}/adherence")
def member_adherence(member_id: str, from_: date | None = Query(default=None, alias="from"), to: date | None = None,
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_profile(db, user, member_id)
    tz = family_tz(db, p.family_id)
    today = datetime.now(ZoneInfo(tz)).date()
    start, _ = day_bounds(from_ or today - timedelta(days=6), tz)
    _, end = day_bounds(to or today, tz)
    return {"from": (from_ or today - timedelta(days=6)).isoformat(), "to": (to or today).isoformat(),
            **adherence(db, p.id, start, end)}


@router.post("/doses/{dose_id}/actions")
def dose_action(dose_id: str, body: DoseAction, idempotency_key: str | None = Header(default=None, max_length=100),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    if idempotency_key:
        prev = db.scalar(select(IdempotencyKey).where(IdempotencyKey.user_id == user.id, IdempotencyKey.key == idempotency_key))
        if prev:
            return prev.response
    d = db.scalar(select(DoseInstance).where(DoseInstance.id == dose_id).with_for_update())
    if not d:
        raise nf()
    p = db.get(MemberProfile, d.member_profile_id)
    m = membership_of(db, user.id, p.family_id)
    if not m or not can_view(p, m, user):
        raise nf()
    if not can_log(p, m, user):
        raise HTTPException(403, "You cannot log doses for this member")
    med = db.get(Medicine, d.medicine_id)
    if d.status == "cancelled":
        raise HTTPException(409, "This dose was cancelled")

    if d.status == body.action and d.acted_by:
        pass  # duplicate tap: idempotent, nothing changes and no extra log
    elif d.status in FINAL and m.role != "admin":
        raise HTTPException(409, "Already logged. Ask a family admin to correct it.")
    else:
        before = {"status": d.status, "acted_by": d.acted_by}
        d.status, d.acted_by, d.acted_at, d.note = body.action, user.id, now(), body.note
        d.is_late = now() > d.due_at + timedelta(minutes=settings.late_after_minutes)  # late entries stay visible
        audit(db, user.id, "dose", d.id, "action", before, {"status": d.status, "late": d.is_late})
        notify_to = [u for u in recipients_for(db, p) if u != user.id]
        if body.action == "no_stock":
            qty = stock_qty(db, med.id)
            if qty:
                db.add(StockLedger(medicine_id=med.id, delta=-qty, reason="reported_empty", created_by=user.id))
            notify(db, recipients_for(db, p), "no_stock", f"{p.name} reports no {med.display_name} left", {"medicine_id": med.id})
        elif body.action == "skipped":
            notify(db, notify_to, "dose_skipped", f"{p.name} skipped {med.display_name}", {"dose_id": d.id})
        elif body.action == "taken" and settings.auto_decrement_stock and stock_qty(db, med.id) is not None:
            db.add(StockLedger(medicine_id=med.id, delta=-1, reason="dose_taken", created_by=user.id))
            db.flush()
            check_low_stock(db, med, p)
    result = dose_out(d, med, True)
    if idempotency_key:
        db.add(IdempotencyKey(user_id=user.id, key=idempotency_key, response=result))
    try:
        db.commit()
    except IntegrityError:  # concurrent duplicate with the same key
        db.rollback()
    return result
