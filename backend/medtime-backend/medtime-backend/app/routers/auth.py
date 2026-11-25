from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import DoseInstance, Family, MemberProfile, Medicine, Membership, User
from ..security import create_token, current_user
from ..services import audit, cancel_future_doses

router = APIRouter(tags=["auth"])


@router.post("/auth/demo")
def demo_login(db: Session = Depends(get_db)):
    """
    Demo-only login. No SMS/email OTP is sent.
    This uses one shared demo account, so never store real personal or medical
    information in this account.
    """
    demo_email = "demo@medtime.local"
    user = db.scalar(select(User).where(User.email == demo_email))
    if not user:
        user = User(name="Demo User", email=demo_email)
        db.add(user)
        db.commit()
        db.refresh(user)

    return {
        "access_token": create_token(user.id),
        "token_type": "bearer",
        "user": {"id": user.id, "name": user.name},
    }


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
