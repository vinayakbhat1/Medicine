import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import (DoseInstance, Family, MemberProfile, Medicine, Membership, OtpCode, Schedule, User, now)
from ..schemas import OtpRequest, OtpVerify
from ..security import create_token, current_user, hash_secret, normalise_identifier
from ..services import audit, cancel_future_doses

router = APIRouter(tags=["auth"])


@router.post("/auth/otp/request")
def request_otp(body: OtpRequest, db: Session = Depends(get_db)):
    ident = normalise_identifier(body.identifier)
    recent = db.scalar(select(func.count()).select_from(OtpCode).where(
        OtpCode.identifier == ident, OtpCode.created_at > now() - timedelta(minutes=15)))
    if recent >= settings.otp_max_per_window:
        raise HTTPException(429, "Too many OTP requests. Try again later.")
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(OtpCode(identifier=ident, code_hash=hash_secret(f"{ident}:{code}"),
                   expires_at=now() + timedelta(seconds=settings.otp_ttl_seconds)))
    db.commit()
    # TODO: deliver `code` through your SMS / email provider here.
    out = {"detail": "OTP sent", "expires_in": settings.otp_ttl_seconds}
    if settings.env == "dev":
        out["dev_code"] = code  # convenience for local development only
    return out


@router.post("/auth/otp/verify")
def verify_otp(body: OtpVerify, db: Session = Depends(get_db)):
    ident = normalise_identifier(body.identifier)
    otp = db.scalar(select(OtpCode).where(OtpCode.identifier == ident, OtpCode.used.is_(False))
                    .order_by(OtpCode.created_at.desc()))
    if not otp or otp.expires_at < now() or otp.attempts >= 5:
        raise HTTPException(400, "Invalid or expired code")
    otp.attempts += 1
    if not hmac.compare_digest(otp.code_hash, hash_secret(f"{ident}:{body.code}")):
        db.commit()
        raise HTTPException(400, "Invalid or expired code")
    otp.used = True
    col = User.email if "@" in ident else User.phone
    user = db.scalar(select(User).where(col == ident))
    if not user:
        user = User(name=(body.name or "User").strip() or "User")
        setattr(user, "email" if "@" in ident else "phone", ident)
        db.add(user)
    db.commit()
    return {"access_token": create_token(user.id), "token_type": "bearer", "user": {"id": user.id, "name": user.name}}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    fams = db.execute(select(Family, Membership).join(Membership, Membership.family_id == Family.id)
                      .where(Membership.user_id == user.id, Membership.status == "active")).all()
    return {"id": user.id, "name": user.name, "phone": user.phone, "email": user.email,
            "families": [{"id": f.id, "name": f.name, "role": m.role} for f, m in fams]}


@router.get("/me/export")
def export_my_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Data export: everything tied to the user's own linked profiles."""
    profiles = list(db.scalars(select(MemberProfile).where(MemberProfile.linked_user_id == user.id)))
    out = {"user": {"id": user.id, "name": user.name, "phone": user.phone, "email": user.email}, "profiles": []}
    for p in profiles:
        meds = list(db.scalars(select(Medicine).where(Medicine.member_profile_id == p.id)))
        doses = list(db.scalars(select(DoseInstance).where(DoseInstance.member_profile_id == p.id).order_by(DoseInstance.due_at)))
        out["profiles"].append({
            "name": p.name, "relationship": p.relationship, "dob": p.dob.isoformat() if p.dob else None,
            "medicines": [{"name": m.display_name, "strength": m.strength, "dose": m.dose, "status": m.status} for m in meds],
            "doses": [{"due_at": d.due_at.isoformat(), "status": d.status, "acted_at": d.acted_at.isoformat() if d.acted_at else None} for d in doses]})
    return out


@router.delete("/me", status_code=204)
def delete_account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Anonymise the account, revoke access and stop future reminders for the user's own profiles."""
    for m in db.scalars(select(Membership).where(Membership.user_id == user.id, Membership.status == "active")):
        admins = db.scalar(select(func.count()).select_from(Membership).where(
            Membership.family_id == m.family_id, Membership.role == "admin", Membership.status == "active"))
        if m.role == "admin" and admins <= 1:
            raise HTTPException(409, "You are the only admin of a family. Promote another admin or remove the family first.")
        m.status = "removed"
    for p in db.scalars(select(MemberProfile).where(MemberProfile.linked_user_id == user.id)):
        cancel_future_doses(db, profile_id=p.id, actor_id=user.id)
        p.is_active = False
        p.name = "Former member"
        p.dob = None
    audit(db, user.id, "user", user.id, "account_deleted")
    user.name, user.phone, user.email, user.is_active = "Deleted user", None, None, False
    db.commit()
