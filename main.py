"""Root entrypoint for running Order Ops Console locally with Uvicorn or deploying on Vercel."""

from __future__ import annotations

import uvicorn

from src.app import app

__all__ = ["app"]

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
