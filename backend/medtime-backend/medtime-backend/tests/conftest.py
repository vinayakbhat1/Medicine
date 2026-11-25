import os
import tempfile

_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(ENV="dev", DATABASE_URL=f"sqlite:///{_db}", RUN_SCHEDULER="false", SECRET_KEY="test-secret")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def login(client, identifier, name="Tester"):
    code = client.post("/api/v1/auth/otp/request", json={"identifier": identifier}).json()["dev_code"]
    r = client.post("/api/v1/auth/otp/verify", json={"identifier": identifier, "code": code, "name": name})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}
