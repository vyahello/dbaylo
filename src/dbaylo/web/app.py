"""FastAPI application factory + a uvicorn entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from dbaylo import __version__
from dbaylo.web import webhook


def create_app() -> FastAPI:
    """Build the FastAPI app with health and webhook routes."""
    app = FastAPI(title="Дбайло", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(webhook.router)
    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint: serve the app with uvicorn."""
    import uvicorn

    uvicorn.run("dbaylo.web.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
