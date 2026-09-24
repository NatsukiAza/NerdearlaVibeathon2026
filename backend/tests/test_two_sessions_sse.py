"""Two fake Sessions + SSE receives a final Cue (no network / no Gemini)."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import wave
from pathlib import Path

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient

# Force fake backend + fast audio pace before app import side effects.
os.environ["SPEECH_BACKEND"] = "fake"
os.environ["AUDIO_PACE"] = "fast"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.setdefault("PRODUCTION_TOKEN", "dev-token")

from app.config import load_settings
import app.config as config_mod

config_mod.settings = load_settings()

from app.main import app
from app.store import store

SAMPLE_RATE = 16000


def _write_tiny_wav(path: Path, duration_ms: int = 1200) -> None:
    """Generate a tiny wav with stdlib wave (do not depend on fixtures/)."""
    nframes = SAMPLE_RATE * duration_ms // 1000
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        frame = (100).to_bytes(2, "little", signed=True)
        wf.writeframes(frame * nframes)


def _parse_sse_chunks(raw: str) -> list[dict]:
    events: list[dict] = []
    blocks = raw.split("\n\n")
    for block in blocks:
        if not block.strip() or block.strip().startswith(":"):
            continue
        event_name = "message"
        data_lines: list[str] = []
        eid = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line.startswith("id:"):
                eid = line[3:].strip()
        if not data_lines:
            continue
        payload = "\n".join(data_lines)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = payload
        events.append({"event": event_name, "data": data, "id": eid})
    return events


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def _reset_store(tmp_path, monkeypatch):
    store._sessions.clear()
    store._production_subs.clear()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    wav = fixtures / "fast-demo.wav"
    _write_tiny_wav(wav, duration_ms=1200)
    monkeypatch.setattr(config_mod.settings, "fixtures_dir", fixtures)
    monkeypatch.setattr(config_mod.settings, "exports_dir", tmp_path / "exports")
    monkeypatch.setattr(config_mod.settings, "speech_backend", "fake")
    monkeypatch.setattr(config_mod.settings, "missing_gemini_api_key", False)
    import app.adapters.audio as audio_mod

    monkeypatch.setattr(audio_mod.settings, "fixtures_dir", fixtures)
    monkeypatch.setattr(audio_mod.settings, "audio_pace", "fast")
    yield
    for rec in list(store._sessions.values()):
        if rec.task and not rec.task.done():
            rec.task.cancel()


@pytest.fixture
async def live_server():
    """Real uvicorn — httpx ASGITransport cannot stream infinite SSE."""
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn failed to start"
    base = f"http://127.0.0.1:{port}"
    try:
        yield base
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)


async def _wait_for_final(session_id: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        rec = store.get(session_id)
        if rec and rec.finals["original"]:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"no final Cue in store for {session_id}")


@pytest.mark.asyncio
async def test_two_sessions_sse_final_cue(live_server: str):
    async with AsyncClient(base_url=live_server, timeout=30.0) as client:
        r1 = await client.post(
            "/api/sessions",
            json={
                "name": "Charla ES A",
                "source": {"kind": "fixture", "path": "fast-demo.wav"},
            },
        )
        assert r1.status_code == 201, r1.text
        s1 = r1.json()
        assert s1["status"] == "idle"

        r2 = await client.post(
            "/api/sessions",
            json={
                "name": "Talk EN B",
                "source": {"kind": "fixture", "path": "fast-demo.wav"},
            },
        )
        assert r2.status_code == 201, r2.text
        s2 = r2.json()

        listed = await client.get("/api/sessions")
        assert listed.status_code == 200
        assert len(listed.json()) == 2

        start1 = await client.post(f"/api/sessions/{s1['id']}/start")
        assert start1.status_code == 200, start1.text
        again = await client.post(f"/api/sessions/{s1['id']}/start")
        assert again.status_code == 409

        start2 = await client.post(f"/api/sessions/{s2['id']}/start")
        assert start2.status_code == 200, start2.text

        await _wait_for_final(s1["id"])
        await _wait_for_final(s2["id"])

        finals: list[dict] = []
        async with client.stream(
            "GET", f"/api/sessions/{s1['id']}/tracks/original/live"
        ) as stream:
            assert stream.status_code == 200
            buf = ""
            async for chunk in stream.aiter_text():
                buf += chunk
                for ev in _parse_sse_chunks(buf):
                    if ev["event"] == "cue" and isinstance(ev["data"], dict):
                        if ev["data"].get("kind") == "final":
                            finals.append(ev["data"])
                if finals:
                    break

        assert finals, "expected final Cue on SSE"
        cue = finals[0]
        assert cue["track"] == "original"
        assert cue["kind"] == "final"
        assert cue["endedAtMs"] is not None
        assert cue["sessionId"] == s1["id"]
        assert cue["sourceLang"] == "es"

        got = None
        async with client.stream(
            "GET", f"/api/sessions/{s2['id']}/tracks/original/live"
        ) as stream:
            buf = ""
            async for chunk in stream.aiter_text():
                buf += chunk
                for ev in _parse_sse_chunks(buf):
                    if ev["event"] == "cue" and isinstance(ev["data"], dict):
                        if ev["data"].get("kind") == "final":
                            got = ev["data"]
                            break
                if got:
                    break
        assert got is not None
        assert got["sourceLang"] == "en"

        stop = await client.post(f"/api/sessions/{s1['id']}/stop")
        assert stop.status_code == 200
        assert stop.json()["status"] == "stopped"

        export = await client.get(
            f"/api/sessions/{s1['id']}/export/original",
            params={"format": "srt"},
        )
        assert export.status_code == 200
        assert "-->" in export.text


@pytest.mark.asyncio
async def test_youtube_url_rejected():
    # Plain ASGI is fine for non-streaming routes.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/sessions",
            json={
                "name": "Bad",
                "source": {
                    "kind": "url",
                    "path": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                },
            },
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_production_events_requires_bearer(live_server: str):
    async with AsyncClient(base_url=live_server, timeout=30.0) as client:
        r = await client.get("/api/production/events")
        assert r.status_code == 401

        await client.post(
            "/api/sessions",
            json={"name": "Prod", "source": {"kind": "mic"}},
        )
        async with client.stream(
            "GET",
            "/api/production/events",
            headers={"Authorization": "Bearer dev-token"},
        ) as resp:
            assert resp.status_code == 200
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                if any(e["event"] == "session" for e in _parse_sse_chunks(buf)):
                    break
            assert any(e["event"] == "session" for e in _parse_sse_chunks(buf))
