from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Medicine, Schedule, StockLedger, User, now
from ..schemas import MedicineCreate, MedicinePatch, ScheduleCreate, StockAdjust
from ..security import current_user
from ..services import (audit, cancel_future_doses, check_low_stock, family_tz, generate_doses, get_medicine,
                        get_profile, stock_qty)

router = APIRouter(tags=["medicines"])


def med_out(med: Medicine, qty) -> dict:
    return {"id": med.id, "member_id": med.member_profile_id, "display_name": med.display_name,
            "strength": med.strength, "dose": med.dose, "route": med.route, "instructions": med.instructions,
            "status": med.status, "low_stock_threshold": med.low_stock_threshold, "stock": qty}


@router.post("/members/{member_id}/medicines", status_code=201)
def create_medicine(member_id: str, body: MedicineCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_profile(db, user, member_id)
    med = Medicine(member_profile_id=p.id, display_name=body.display_name.strip(), strength=body.strength.strip(),
                   dose=body.dose.strip(), route=body.route, instructions=body.instructions,
                   low_stock_threshold=body.low_stock_threshold, created_by=user.id)
    db.add(med)
    db.flush()
    if body.initial_stock is not None:
        db.add(StockLedger(medicine_id=med.id, delta=body.initial_stock, reason="initial_stock", created_by=user.id))
    audit(db, user.id, "medicine", med.id, "created", after={"name": med.display_name, "dose": med.dose})
    db.commit()
    return med_out(med, body.initial_stock)


@router.get("/members/{member_id}/medicines")
def list_medicines(member_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_profile(db, user, member_id)
    meds = db.scalars(select(Medicine).where(Medicine.member_profile_id == p.id).order_by(Medicine.created_at))
    return [med_out(m, stock_qty(db, m.id)) for m in meds]


@router.patch("/medicines/{medicine_id}")
def patch_medicine(medicine_id: str, body: MedicinePatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    med, p, _ = get_medicine(db, user, medicine_id)
    changes = body.model_dump(exclude_unset=True)
    before = {k: getattr(med, k) for k in changes}
    for k, v in changes.items():
        setattr(med, k, v)
    if "status" in changes and changes["status"] != before["status"]:
        if changes["status"] in ("paused", "discontinued"):
            cancel_future_doses(db, medicine_id=med.id, actor_id=user.id)
            if changes["status"] == "discontinued":
                for s in db.scalars(select(Schedule).where(Schedule.medicine_id == med.id)):
                    s.status = "ended"
        elif changes["status"] == "active":
            for s in db.scalars(select(Schedule).where(Schedule.medicine_id == med.id, Schedule.status == "active")):
                generate_doses(db, s, med)
    audit(db, user.id, "medicine", med.id, "updated", before, changes)
    db.commit()
    return med_out(med, stock_qty(db, med.id))


@router.post("/medicines/{medicine_id}/schedules", status_code=201)
def create_schedule(medicine_id: str, body: ScheduleCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    med, p, _ = get_medicine(db, user, medicine_id)
    if med.status == "discontinued":
        raise HTTPException(409, "Medicine is discontinued")
    if body.end_date and body.end_date < body.start_date:
        raise HTTPException(422, "end_date cannot be before start_date")
    sched = Schedule(medicine_id=med.id, times=body.times, days_of_week=body.days_of_week, start_date=body.start_date,
                     end_date=body.end_date, timezone=body.timezone or family_tz(db, p.family_id), created_at=now())
    db.add(sched)
    db.flush()
    made = 0
    if med.status == "active":
        made = generate_doses(db, sched, med)
    audit(db, user.id, "schedule", sched.id, "created", after={"times": sched.times, "days": sched.days_of_week})
    db.commit()
    return {"id": sched.id, "times": sched.times, "days_of_week": sched.days_of_week,
            "start_date": sched.start_date.isoformat(), "end_date": sched.end_date.isoformat() if sched.end_date else None,
            "timezone": sched.timezone, "doses_generated": made}


@router.get("/medicines/{medicine_id}/schedules")
def list_schedules(medicine_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    med, _, _ = get_medicine(db, user, medicine_id)
    return [{"id": s.id, "times": s.times, "days_of_week": s.days_of_week, "start_date": s.start_date.isoformat(),
             "end_date": s.end_date.isoformat() if s.end_date else None, "status": s.status, "timezone": s.timezone}
            for s in db.scalars(select(Schedule).where(Schedule.medicine_id == med.id))]


@router.get("/medicines/{medicine_id}/stock")
def read_stock(medicine_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    med, _, _ = get_medicine(db, user, medicine_id)
    q = stock_qty(db, med.id)
    return {"medicine_id": med.id, "quantity": q, "low_stock_threshold": med.low_stock_threshold,
            "is_low": q is not None and q <= med.low_stock_threshold,
            "note": "Stock is adjusted manually unless AUTO_DECREMENT_STOCK is enabled."}


@router.post("/medicines/{medicine_id}/stock-adjustments", status_code=201)
def adjust_stock(medicine_id: str, body: StockAdjust, user: User = Depends(current_user), db: Session = Depends(get_db)):
    med, p, _ = get_medicine(db, user, medicine_id)
    current = stock_qty(db, med.id) or 0
    if current + body.delta < 0:
        raise HTTPException(422, "Stock cannot go below zero")
    db.add(StockLedger(medicine_id=med.id, delta=body.delta, reason=body.reason, created_by=user.id))
    audit(db, user.id, "medicine", med.id, "stock_adjusted", {"quantity": current}, {"quantity": current + body.delta})
    db.flush()
    check_low_stock(db, med, p)
    db.commit()
    return {"medicine_id": med.id, "quantity": current + body.delta}
