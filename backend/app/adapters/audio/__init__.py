"""Audio adapters: fixture, mic queue, url — all yield PCM s16le 16 kHz mono."""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings
from app.domain.models import BYTES_PER_SAMPLE, CHUNK_BYTES, CHUNK_MS, SAMPLE_RATE

YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
        "music.youtube.com",
    }
)


def is_youtube_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if host in YOUTUBE_HOSTS:
        return True
    return host.endswith(".youtube.com")


def should_pace_realtime(path: str | None = None) -> bool:
    """Realtime sleep by default. Skip if AUDIO_PACE=fast or path contains 'fast'."""
    # AUDIO_PACE documented in comment only (do not edit env.md).
    if settings.audio_pace == "fast" or os.getenv("AUDIO_PACE", "").strip().lower() == "fast":
        return False
    if path and "fast" in path.lower():
        return False
    return True


class _RealtimePacer:
    """Sleep until the wall clock reaches the audio position (t0 + samples/rate).

    Sleeping a fixed chunk duration per chunk drifts (send time + timer
    granularity, ~15 ms on Windows) and after a minute the fixture is several
    seconds behind real time. Pacing against a deadline keeps it live.
    """

    def __init__(self) -> None:
        self._t0: float | None = None

    async def wait_until(self, samples_total: int) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._t0 is None:
            self._t0 = now
        target = self._t0 + samples_total / SAMPLE_RATE
        delay = target - now
        if delay > 0:
            await asyncio.sleep(delay)


async def _yield_pcm_chunks(
    pcm: bytes,
    *,
    pace: bool,
) -> AsyncIterator[tuple[bytes, int]]:
    samples_total = 0
    offset = 0
    pacer = _RealtimePacer()
    while offset < len(pcm):
        chunk = pcm[offset : offset + CHUNK_BYTES]
        if len(chunk) % BYTES_PER_SAMPLE:
            chunk = chunk[: len(chunk) - (len(chunk) % BYTES_PER_SAMPLE)]
        if not chunk:
            break
        offset += len(chunk)
        samples_total += len(chunk) // BYTES_PER_SAMPLE
        yield chunk, samples_total
        if pace:
            await pacer.wait_until(samples_total)


def _wav_to_pcm_s16le_16k_mono(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if sw != 2 or nch != 1 or rate != SAMPLE_RATE:
        # re-encode via ffmpeg for non-matching wav
        return _ffmpeg_file_to_pcm(path)
    return frames


def _ffmpeg_file_to_pcm(path: Path) -> bytes:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed for fixture {path}: {proc.stderr.decode(errors='replace')}"
        )
    return proc.stdout


def _ffmpeg_url_to_pcm_stream(url: str) -> subprocess.Popen[bytes]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        url,
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "pipe:1",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


async def fixture_pcm(
    relative_path: str,
    fixtures_dir: Path | None = None,
) -> AsyncIterator[tuple[bytes, int]]:
    """Read fixture relative to fixtures/. Missing file raises FileNotFoundError."""
    base = fixtures_dir or settings.fixtures_dir
    # prevent path escape
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"invalid fixture path: {relative_path}")
    path = (base / rel).resolve()
    if not str(path).startswith(str(base.resolve())):
        raise ValueError(f"fixture path escapes fixtures/: {relative_path}")
    if not path.is_file():
        raise FileNotFoundError(f"fixture not found: {relative_path}")

    suffix = path.suffix.lower()
    if suffix == ".wav":
        try:
            pcm = _wav_to_pcm_s16le_16k_mono(path)
        except wave.Error:
            pcm = _ffmpeg_file_to_pcm(path)
    else:
        pcm = _ffmpeg_file_to_pcm(path)

    pace = should_pace_realtime(relative_path)
    async for item in _yield_pcm_chunks(pcm, pace=pace):
        yield item


async def url_pcm(url: str) -> AsyncIterator[tuple[bytes, int]]:
    if is_youtube_url(url):
        raise ValueError("YouTube hosts are not allowed; use a fixture or direct audio/HLS URL")
    pace = should_pace_realtime(url)
    proc = await asyncio.to_thread(_ffmpeg_url_to_pcm_stream, url)
    assert proc.stdout is not None
    samples_total = 0
    pacer = _RealtimePacer()
    try:
        while True:
            chunk = await asyncio.to_thread(proc.stdout.read, CHUNK_BYTES)
            if not chunk:
                break
            if len(chunk) % BYTES_PER_SAMPLE:
                chunk = chunk[: len(chunk) - (len(chunk) % BYTES_PER_SAMPLE)]
            if not chunk:
                break
            samples_total += len(chunk) // BYTES_PER_SAMPLE
            yield chunk, samples_total
            if pace:
                await pacer.wait_until(samples_total)
    finally:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


async def mic_pcm(queue: asyncio.Queue[bytes | None]) -> AsyncIterator[tuple[bytes, int]]:
    """Consume binary PCM frames from a mic WebSocket queue. None = end."""
    samples_total = 0
    buf = bytearray()
    while True:
        frame = await queue.get()
        if frame is None:
            break
        buf.extend(frame)
        while len(buf) >= CHUNK_BYTES:
            chunk = bytes(buf[:CHUNK_BYTES])
            del buf[:CHUNK_BYTES]
            samples_total += len(chunk) // BYTES_PER_SAMPLE
            yield chunk, samples_total
    # flush remainder
    if len(buf) >= BYTES_PER_SAMPLE:
        rem = len(buf) - (len(buf) % BYTES_PER_SAMPLE)
        chunk = bytes(buf[:rem])
        samples_total += len(chunk) // BYTES_PER_SAMPLE
        yield chunk, samples_total


def synth_silence_wav_bytes(duration_ms: int = 1500, path: Path | None = None) -> bytes:
    """Tiny wav helper for tests (stdlib wave)."""
    nframes = SAMPLE_RATE * duration_ms // 1000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"\x00\x00" * nframes)
    data = buf.getvalue()
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data
