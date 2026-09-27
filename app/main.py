"""Point d'entrée FastAPI."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import ROOT
from app.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # `on_event("startup")` est déprécié depuis FastAPI 0.93 et émet un
    # avertissement à chaque démarrage.
    init_db()
    yield


app = FastAPI(title="Nindō", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "web" / "static")),
          name="static")
