import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  (register tables)
from .config import settings
from .database import Base, engine
from .routers import auth, doses, families, medicines
from .worker import start_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.env != "dev" and settings.secret_key.startswith("dev-only"):
        raise RuntimeError("Set a strong SECRET_KEY before running outside dev")
    Base.metadata.create_all(engine)  # use Alembic migrations in production
    sched = start_scheduler() if settings.run_scheduler else None
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="MedTime API", version="0.1.0", lifespan=lifespan,
              description="Family medicine reminder & adherence API. Records self-reported dose status; "
                          "it is not a medical device and gives no medical advice.")
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
for r in (auth.router, families.router, medicines.router, doses.router):
    app.include_router(r, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
