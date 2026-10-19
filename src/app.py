"""FastAPI application factory and static frontend mounting for Order Ops Console."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.database import create_all_tables
from src.demo_data import ensure_sample_files_on_disk
from src.routes import router as api_router

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_all_tables()
    try:
        ensure_sample_files_on_disk()
    except OSError:
        # Serverless environments may have read-only root directories;
        # sample CSV downloads still work from in-memory generation.
        pass
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Order Ops Console",
        description=(
            "Order operations console reconciling Shopify order exports, courier tracking sheets, "
            "and WhatsApp customer complaints for Indian D2C brands."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app


app = create_app()
