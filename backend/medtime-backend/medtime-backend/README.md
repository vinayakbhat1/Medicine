# MedTime API (MVP backend)

FastAPI + SQLAlchemy 2 + PostgreSQL (SQLite for quick local runs). Implements the MVP from the developer handoff deck:
OTP login, families and roles, member profiles, medicines and schedules, dose instances, Taken / Skipped / No stock,
missed-dose worker, stock ledger, dashboard, adherence, audit trail and privacy endpoints.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional; defaults work for dev
uvicorn app.main:app --reload
```
Open http://localhost:8000/docs for interactive API docs. Run tests: `pytest`.

With PostgreSQL: `docker compose up --build`.

## Try it
1. `POST /api/v1/auth/otp/request` `{"identifier":"+919999999999"}`. In `ENV=dev` the response includes `dev_code`.
2. `POST /api/v1/auth/otp/verify` with the code and a `name`. You get a bearer `access_token`.
3. `POST /api/v1/families`, then add members, medicines, and `POST /medicines/{id}/schedules`.
4. `GET /families/{id}/dashboard` and `POST /doses/{id}/actions` (`taken` | `skipped` | `no_stock`).

## Endpoints (all under `/api/v1`)
| Area | Endpoints |
|---|---|
| Auth | `POST /auth/otp/request`, `POST /auth/otp/verify`, `GET /me`, `GET /me/export`, `DELETE /me` |
| Family | `POST /families`, `GET /families`, `GET\|POST /families/{id}/members`, `DELETE /families/{id}/members/{profile_id}`, `PATCH /families/{id}/consent`, `GET /families/{id}/dashboard?date=` |
| Invites | `POST /invitations/{token}/accept`, `POST /invitations/{token}/decline` |
| Medicines | `POST\|GET /members/{id}/medicines`, `PATCH /medicines/{id}`, `POST\|GET /medicines/{id}/schedules` |
| Stock | `GET /medicines/{id}/stock`, `POST /medicines/{id}/stock-adjustments` |
| Doses | `GET /members/{id}/doses?from=&to=`, `POST /doses/{id}/actions`, `GET /members/{id}/adherence?from=&to=` |

## Rules implemented
- **Access control on every request.** Non-members and cross-family calls get 404. Members see only their own profile. Admins can log doses for dependents only.
- **Dose instances** are generated for 14 days ahead in the schedule's timezone, and extended nightly. History before the schedule existed is never invented.
- **Idempotent dose actions.** Repeat taps (with or without an `Idempotency-Key` header) change nothing and write one audit entry. Correcting a different final status needs an admin.
- **Missed** is set by the worker after `MISSED_GRACE_MINUTES` (default 120) and never overwrites a user action. Late logging is allowed and flagged `is_late`.
- **Stock** is a ledger and is manual by default. `AUTO_DECREMENT_STOCK=true` is opt-in. "No stock" zeroes the ledger and alerts admins. Skipped never decrements.
- **Adherence %** = taken ÷ (taken + skipped + missed + no_stock), cancelled excluded, with the disclaimer in the response.
- **Privacy.** Push payloads carry no medicine names. Admin sees a member's private dose notes only for dependents or with `share_notes` consent. Data export and account deletion are included.

## Before production
- Plug an SMS/email provider into `request_otp`, and FCM/APNs into `send_push` (`app/services.py`).
- Set `ENV=production`, a strong `SECRET_KEY`, and real `CORS_ORIGINS`. The app refuses to start with the dev secret.
- Replace `create_all` with Alembic migrations. Add the notification-delivery tracking, HTTPS, backups and monitoring from the deck.
- Per-IP rate limiting for the OTP endpoints (a per-number limit exists).
- Get the privacy/security review and the missed-grace sign-off from the product owner.
