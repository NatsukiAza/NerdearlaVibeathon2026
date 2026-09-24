"""FastAPI entrypoint — conference live captions API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Conference live captions",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "speechBackend": settings.speech_backend,
        "missingGeminiKey": str(settings.missing_gemini_api_key).lower(),
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
