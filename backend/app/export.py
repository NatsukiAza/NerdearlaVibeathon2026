"""ExportDeTranscript — SRT / VTT / TXT from final Cues only."""

from __future__ import annotations

from pathlib import Path

from app.domain.models import Cue, ExportFormat


def _fmt_ts(ms: int, *, vtt: bool = False) -> str:
    if ms < 0:
        ms = 0
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1000
    frac = ms % 1000
    sep = "." if vtt else ","
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{frac:03d}"


def _ended_at(cues: list[Cue], idx: int) -> int:
    c = cues[idx]
    if c.endedAtMs is not None:
        return c.endedAtMs
    if idx + 1 < len(cues):
        return cues[idx + 1].startedAtMs
    return c.startedAtMs + 3000


def to_srt(cues: list[Cue]) -> str:
    finals = [c for c in cues if c.kind == "final"]
    lines: list[str] = []
    for i, c in enumerate(finals):
        start = _fmt_ts(c.startedAtMs)
        end = _fmt_ts(_ended_at(finals, i))
        lines.append(str(i + 1))
        lines.append(f"{start} --> {end}")
        lines.append(c.text)
        lines.append("")
    return "\n".join(lines)


def to_vtt(cues: list[Cue]) -> str:
    finals = [c for c in cues if c.kind == "final"]
    lines = ["WEBVTT", ""]
    for i, c in enumerate(finals):
        start = _fmt_ts(c.startedAtMs, vtt=True)
        end = _fmt_ts(_ended_at(finals, i), vtt=True)
        lines.append(f"{start} --> {end}")
        lines.append(c.text)
        lines.append("")
    return "\n".join(lines)


def to_txt(cues: list[Cue]) -> str:
    finals = [c for c in cues if c.kind == "final"]
    blocks: list[str] = []
    for i, c in enumerate(finals):
        start = _fmt_ts(c.startedAtMs)
        end = _fmt_ts(_ended_at(finals, i))
        blocks.append(f"[{start} → {end}]")
        blocks.append(c.text)
        blocks.append("")
    return "\n".join(blocks)


def render(cues: list[Cue], fmt: ExportFormat) -> str:
    if fmt == "srt":
        return to_srt(cues)
    if fmt == "vtt":
        return to_vtt(cues)
    return to_txt(cues)


def media_type(fmt: ExportFormat) -> str:
    if fmt == "srt":
        return "application/x-subrip"
    if fmt == "vtt":
        return "text/vtt"
    return "text/plain"


def write_exports(session_id: str, tracks: dict[str, list[Cue]], directory: Path) -> None:
    """Write {lang}.{srt|vtt|txt} under directory for each track."""
    directory.mkdir(parents=True, exist_ok=True)
    for lang, cues in tracks.items():
        for fmt in ("srt", "vtt", "txt"):
            path = directory / f"{lang}.{fmt}"
            path.write_text(render(cues, fmt), encoding="utf-8")  # type: ignore[arg-type]
