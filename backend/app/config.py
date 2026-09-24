"""Runtime configuration and SPEECH_BACKEND resolution (env.md 5 rules)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

def _resolve_roots() -> tuple[Path, Path]:
    """Locate repo root and the backend package root.

    Local checkout: ``backend/app/config.py`` → repo root is the parent of ``backend/``.
    Docker image: the Dockerfile copies ``backend/`` to ``/app``, and Compose mounts
    ``fixtures/`` and ``glossary/`` on that same directory.
    """
    here = Path(__file__).resolve()
    backend_root = here.parents[1]
    for parent in here.parents:
        if (parent / "fixtures").is_dir():
            return parent, backend_root
    sibling = backend_root.parent
    if (sibling / "backend").is_dir():
        return sibling, backend_root
    return backend_root, backend_root


REPO_ROOT, BACKEND_ROOT = _resolve_roots()

# Load repo-root .env if present (ops creates it; we do not).
_env_path = REPO_ROOT / ".env"
if _env_path.is_file():
    load_dotenv(_env_path)
else:
    load_dotenv()  # cwd fallback


@dataclass
class Settings:
    gemini_api_key: str
    speech_backend: str  # "fake" | "gemini"
    missing_gemini_api_key: bool
    production_token: str
    cors_origin: str
    api_host: str
    api_port: int
    fixtures_dir: Path
    exports_dir: Path
    conference_glossary_path: Path
    audio_pace: str  # "" | "fast" — comment-only contract; see AUDIO_PACE below


# AUDIO_PACE: when set to "fast" (or fixture path contains "fast"), audio adapters
# skip realtime sleep so tests finish quickly. Default is realtime pacing.


def _resolve_speech_backend(key: str, requested: str | None) -> tuple[str, bool]:
    """
    env.md rules:
    1. SPEECH_BACKEND=fake → fake (even with key)
    2. SPEECH_BACKEND=gemini + key → gemini
    3. omitted + key → gemini
    4. omitted + no key → fake
    5. SPEECH_BACKEND=gemini + no key → start in fake, mark sessions degraded
       with missing_gemini_api_key (do not crash)
    """
    req = (requested or "").strip().lower() or None
    has_key = bool(key and key.strip())

    if req == "fake":
        return "fake", False
    if req == "gemini":
        if has_key:
            return "gemini", False
        return "fake", True  # rule 5
    # omitted
    if has_key:
        return "gemini", False
    return "fake", False


def load_settings() -> Settings:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    requested = os.getenv("SPEECH_BACKEND")
    backend, missing = _resolve_speech_backend(key, requested)
    return Settings(
        gemini_api_key=key,
        speech_backend=backend,
        missing_gemini_api_key=missing,
        production_token=os.getenv("PRODUCTION_TOKEN", "dev-token"),
        cors_origin=os.getenv("CORS_ORIGIN", "http://localhost:5173"),
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("API_PORT", "8000")),
        fixtures_dir=REPO_ROOT / "fixtures",
        exports_dir=BACKEND_ROOT / "var" / "exports",
        conference_glossary_path=REPO_ROOT / "glossary" / "nerdearla.json",
        audio_pace=os.getenv("AUDIO_PACE", "").strip().lower(),
    )


settings = load_settings()
