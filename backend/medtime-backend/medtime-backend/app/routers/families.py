import secrets
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Family, Invitation, MemberProfile, Medicine, Membership, User, now
from ..schemas import ConsentUpdate, FamilyCreate, MemberCreate
from ..security import current_user, hash_secret, normalise_identifier
from ..services import (adherence, audit, cancel_future_doses, check_low_stock, day_bounds, dose_out, family_tz,
                        membership_of, note_visible, nf, require_admin, require_member, stock_qty, visible_profiles)
from ..models import DoseInstance

router = APIRouter(tags=["families"])


def profile_out(p: MemberProfile) -> dict:
    return {"id": p.id, "name": p.name, "relationship": p.relationship, "dob": p.dob.isoformat() if p.dob else None,
            "is_dependent": p.is_dependent, "has_login": p.linked_user_id is not None}


@router.post("/families", status_code=201)
def create_family(body: FamilyCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    fam = Family(name=body.name.strip(), created_by=user.id, timezone=body.timezone)
    db.add(fam)
    db.flush()
    db.add(Membership(family_id=fam.id, user_id=user.id, role="admin", consent_flags={}))
    me = MemberProfile(family_id=fam.id, linked_user_id=user.id, name=body.creator_name or user.name,
                       relationship="Self", is_dependent=False)
    db.add(me)
    audit(db, user.id, "family", fam.id, "created", after={"name": fam.name})
    db.commit()
    return {"id": fam.id, "name": fam.name, "timezone": fam.timezone, "role": "admin", "my_profile_id": me.id}


@router.get("/families")
def my_families(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Family, Membership).join(Membership, Membership.family_id == Family.id)
                      .where(Membership.user_id == user.id, Membership.status == "active")).all()
    return [{"id": f.id, "name": f.name, "timezone": f.timezone, "role": m.role} for f, m in rows]


@router.get("/families/{family_id}/members")
def list_members(family_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, family_id)
    return [profile_out(p) for p in visible_profiles(db, user, m)]


@router.post("/families/{family_id}/members", status_code=201)
def add_member(family_id: str, body: MemberCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_admin(db, user, family_id)
    invite = body.invite_identifier and not body.is_dependent
    p = MemberProfile(family_id=family_id, name=body.name.strip(), relationship=body.relationship, dob=body.dob,
                      is_dependent=not invite)
    db.add(p)
    db.flush()
    out = {"profile": profile_out(p)}
    if invite:
        token = secrets.token_urlsafe(24)
        db.add(Invitation(family_id=family_id, profile_id=p.id, identifier=normalise_identifier(body.invite_identifier),
                          token_hash=hash_secret(token), expires_at=now() + timedelta(days=7), created_by=user.id))
        out["invite_token"] = token  # share via secure link / SMS; shown only once
    audit(db, user.id, "member_profile", p.id, "created", after={"name": p.name, "dependent": p.is_dependent})
    db.commit()
    return out


@router.delete("/families/{family_id}/members/{profile_id}", status_code=204)
def remove_member(family_id: str, profile_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_admin(db, user, family_id)
    p = db.get(MemberProfile, profile_id)
    if not p or p.family_id != family_id or not p.is_active:
        raise nf()
    if p.linked_user_id:
        target = membership_of(db, p.linked_user_id, family_id)
        if target:
            if target.role == "admin":
                admins = db.scalar(select(func.count()).select_from(Membership).where(
                    Membership.family_id == family_id, Membership.role == "admin", Membership.status == "active"))
                if admins <= 1:
                    raise HTTPException(409, "Cannot remove the last admin")
            target.status = "removed"
    p.is_active = False
    n = cancel_future_doses(db, profile_id=p.id, actor_id=user.id)
    audit(db, user.id, "member_profile", p.id, "removed", after={"cancelled_doses": n})
    db.commit()


@router.patch("/families/{family_id}/consent")
def update_my_consent(family_id: str, body: ConsentUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, family_id)
    before = dict(m.consent_flags or {})
    m.consent_flags = {**before, "share_notes": body.share_notes}
    audit(db, user.id, "membership", m.id, "consent_changed", before, m.consent_flags)
    db.commit()
    return {"consent_flags": m.consent_flags}


@router.post("/invitations/{token}/accept")
def accept_invite(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == hash_secret(token)))
    if not inv or inv.status != "pending" or inv.expires_at < now():
        raise HTTPException(400, "Invitation is invalid or expired")
    mine = {normalise_identifier(x) for x in (user.phone, user.email) if x}
    if inv.identifier not in mine:
        raise HTTPException(403, "This invitation was sent to a different mobile number or email")
    mem = db.scalar(select(Membership).where(Membership.family_id == inv.family_id, Membership.user_id == user.id))
    if mem:
        mem.status, mem.role = "active", mem.role or "member"
    else:
        db.add(Membership(family_id=inv.family_id, user_id=user.id, role="member", consent_flags={}))
    p = db.get(MemberProfile, inv.profile_id)
    p.linked_user_id, p.is_dependent = user.id, False
    inv.status = "accepted"
    audit(db, user.id, "invitation", inv.id, "accepted")
    db.commit()
    return {"family_id": inv.family_id, "profile_id": p.id}


@router.post("/invitations/{token}/decline", status_code=204)
def decline_invite(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == hash_secret(token)))
    if not inv or inv.status != "pending":
        raise HTTPException(400, "Invitation is invalid or expired")
    inv.status = "declined"
    audit(db, user.id, "invitation", inv.id, "declined")
    db.commit()


@router.get("/families/{family_id}/dashboard")
def dashboard(family_id: str, on_date: date | None = Query(default=None, alias="date"),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, family_id)
    tz = family_tz(db, family_id)
    day = on_date or datetime.now(ZoneInfo(tz)).date()
    start, end = day_bounds(day, tz)
    members, totals = [], {"scheduled": 0, "due": 0, "taken": 0, "skipped": 0, "missed": 0, "no_stock": 0}
    alerts = []
    for p in visible_profiles(db, user, m):
        rows = db.execute(select(DoseInstance, Medicine).join(Medicine, Medicine.id == DoseInstance.medicine_id)
                          .where(DoseInstance.member_profile_id == p.id, DoseInstance.due_at >= start,
                                 DoseInstance.due_at < end, DoseInstance.status != "cancelled")
                          .order_by(DoseInstance.due_at)).all()
        show = note_visible(db, user, p)
        doses = [dose_out(d, med, show) for d, med in rows]
        counts = {k: 0 for k in totals}
        for d in doses:
            counts[d["status"]] += 1
            totals[d["status"]] += 1
        members.append({**profile_out(p), "counts": counts, "doses": doses,
                        "adherence_7d": adherence(db, p.id, now() - timedelta(days=7), now())})
        for med in db.scalars(select(Medicine).where(Medicine.member_profile_id == p.id, Medicine.status == "active")):
            q = stock_qty(db, med.id)
            if q is not None and q <= med.low_stock_threshold:
                alerts.append({"type": "low_stock", "member_id": p.id, "medicine_id": med.id,
                               "medicine_name": med.display_name, "quantity": q})
    return {"date": day.isoformat(), "timezone": tz, "totals": totals, "members": members, "stock_alerts": alerts}
