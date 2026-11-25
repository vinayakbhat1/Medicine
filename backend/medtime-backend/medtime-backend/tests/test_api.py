import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models import DoseInstance, now
from app.services import mark_missed
from conftest import login

API = "/api/v1"


def phone():
    return "+91" + str(uuid.uuid4().int)[:10]


def setup_family(client):
    h = login(client, phone(), "Admin")
    fam = client.post(f"{API}/families", json={"name": "Sharma", "timezone": "Asia/Kolkata"}, headers=h).json()
    kid = client.post(f"{API}/families/{fam['id']}/members", json={"name": "Aarav", "relationship": "Child"}, headers=h).json()["profile"]
    med = client.post(f"{API}/members/{kid['id']}/medicines", json={"display_name": "Vitamin D3", "strength": "60000 IU",
                      "dose": "1 capsule", "low_stock_threshold": 2, "initial_stock": 3}, headers=h).json()
    sched = client.post(f"{API}/medicines/{med['id']}/schedules", json={"times": ["09:00", "21:00"],
                        "start_date": date.today().isoformat()}, headers=h)
    return h, fam, kid, med, sched


def test_otp_rules(client):
    ident = phone()
    code = client.post(f"{API}/auth/otp/request", json={"identifier": ident}).json()["dev_code"]
    assert client.post(f"{API}/auth/otp/verify", json={"identifier": ident, "code": "000000" if code != "000000" else "111111"}).status_code == 400
    assert client.post(f"{API}/auth/otp/verify", json={"identifier": ident, "code": code}).status_code == 200
    assert client.post(f"{API}/auth/otp/verify", json={"identifier": ident, "code": code}).status_code == 400  # single use


def test_otp_rate_limit(client):
    ident = phone()
    codes = [client.post(f"{API}/auth/otp/request", json={"identifier": ident}).status_code for _ in range(6)]
    assert codes[-1] == 429


def test_requires_auth(client):
    assert client.get(f"{API}/families").status_code == 401


def test_full_dose_flow_and_idempotency(client):
    h, fam, kid, med, sched = setup_family(client)
    assert sched.status_code == 201 and sched.json()["doses_generated"] > 10
    doses = client.get(f"{API}/members/{kid['id']}/doses", params={"from": (date.today() + timedelta(days=1)).isoformat()}, headers=h).json()["items"]
    assert len(doses) == 2
    did = doses[0]["id"]
    hdr = {**h, "Idempotency-Key": "tap-1"}
    a = client.post(f"{API}/doses/{did}/actions", json={"action": "taken"}, headers=hdr)
    b = client.post(f"{API}/doses/{did}/actions", json={"action": "taken"}, headers=hdr)
    c = client.post(f"{API}/doses/{did}/actions", json={"action": "taken"}, headers=h)  # no key, still idempotent
    assert a.status_code == b.status_code == c.status_code == 200
    assert a.json()["status"] == "taken" and a.json() == b.json()
    from app.models import AuditEvent
    with SessionLocal() as db:
        n = len(list(db.scalars(select(AuditEvent).where(AuditEvent.entity_id == did, AuditEvent.action == "action"))))
    assert n == 1  # duplicate taps did not create duplicate log entries


def test_missed_worker_and_late_action(client):
    h, fam, kid, med, _ = setup_family(client)
    with SessionLocal() as db:
        d = db.scalars(select(DoseInstance).where(DoseInstance.member_profile_id == kid["id"]).order_by(DoseInstance.due_at)).first()
        d.due_at = now() - timedelta(hours=3)
        db.commit()
        did = d.id
        assert mark_missed(db) >= 1
    got = client.get(f"{API}/members/{kid['id']}/doses", params={"from": date.today().isoformat(), "to": date.today().isoformat()}, headers=h).json()
    r = client.post(f"{API}/doses/{did}/actions", json={"action": "taken"}, headers=h)
    assert r.json()["status"] == "taken" and r.json()["is_late"] is True  # late entry stays auditable


def test_cross_family_access_fails_safely(client):
    h1, fam1, kid1, med1, _ = setup_family(client)
    h2 = login(client, phone(), "Stranger")
    assert client.get(f"{API}/families/{fam1['id']}/dashboard", headers=h2).status_code == 404
    assert client.get(f"{API}/members/{kid1['id']}/doses", headers=h2).status_code == 404
    assert client.post(f"{API}/members/{kid1['id']}/medicines", json={"display_name": "X", "dose": "1"}, headers=h2).status_code == 404
    assert client.get(f"{API}/medicines/{med1['id']}/stock", headers=h2).status_code == 404
    assert client.post(f"{API}/medicines/{med1['id']}/stock-adjustments", json={"delta": 5}, headers=h2).status_code == 404


def test_member_sees_only_own_data_and_invite_flow(client):
    h, fam, kid, med, _ = setup_family(client)
    ident = phone()
    inv = client.post(f"{API}/families/{fam['id']}/members", json={"name": "Mother", "relationship": "Mother",
                      "is_dependent": False, "invite_identifier": ident}, headers=h).json()
    hm = login(client, ident, "Mother")
    wrong = login(client, phone(), "Other")
    assert client.post(f"{API}/invitations/{inv['invite_token']}/accept", headers=wrong).status_code == 403
    assert client.post(f"{API}/invitations/{inv['invite_token']}/accept", headers=hm).status_code == 200
    dash = client.get(f"{API}/families/{fam['id']}/dashboard", headers=hm).json()
    assert [m["name"] for m in dash["members"]] == ["Mother"]  # not the child, not the admin
    assert client.get(f"{API}/members/{kid['id']}/doses", headers=hm).status_code == 404
    assert client.post(f"{API}/families/{fam['id']}/members", json={"name": "Z"}, headers=hm).status_code == 403


def test_stock_and_low_stock_alert(client):
    h, fam, kid, med, _ = setup_family(client)
    assert client.get(f"{API}/medicines/{med['id']}/stock", headers=h).json()["is_low"] is False
    assert client.post(f"{API}/medicines/{med['id']}/stock-adjustments", json={"delta": -2}, headers=h).status_code == 201
    assert client.get(f"{API}/medicines/{med['id']}/stock", headers=h).json()["is_low"] is True
    assert client.post(f"{API}/medicines/{med['id']}/stock-adjustments", json={"delta": -50}, headers=h).status_code == 422
    dash = client.get(f"{API}/families/{fam['id']}/dashboard", headers=h).json()
    assert dash["stock_alerts"][0]["medicine_name"] == "Vitamin D3"


def test_pause_cancels_future_and_validation(client):
    h, fam, kid, med, _ = setup_family(client)
    assert client.patch(f"{API}/medicines/{med['id']}", json={"status": "paused"}, headers=h).json()["status"] == "paused"
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.get(f"{API}/members/{kid['id']}/doses", params={"from": tomorrow}, headers=h).json()["items"] == []
    bad = client.post(f"{API}/medicines/{med['id']}/schedules", json={"times": ["25:00"], "start_date": tomorrow}, headers=h)
    assert bad.status_code == 422


def test_adherence_formula(client):
    h, fam, kid, med, _ = setup_family(client)
    with SessionLocal() as db:
        ds = list(db.scalars(select(DoseInstance).where(DoseInstance.member_profile_id == kid["id"]).order_by(DoseInstance.due_at).limit(4)))
        for i, d in enumerate(ds):
            d.due_at = now() - timedelta(hours=10 + i)
            d.status = ["taken", "taken", "taken", "missed"][i]
        db.commit()
    r = client.get(f"{API}/members/{kid['id']}/adherence", headers=h).json()
    assert r["eligible"] == 4 and r["taken"] == 3 and r["adherence_percent"] == 75.0
