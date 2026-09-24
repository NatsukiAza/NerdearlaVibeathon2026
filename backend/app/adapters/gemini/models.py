"""Gemini model id strings — ONLY place to change if names fail at runtime."""

SPEECH_MODEL = "gemini-3.5-transcribe-live"
TRANSLATOR_MODEL = "gemini-3.5-flash"

# Tried in order when TRANSLATOR_MODEL answers 429/5xx (24 sep 2026: gemini-3.5-flash
# returned 503 "high demand" for minutes at a time; -lite answered in 1-2 s).
TRANSLATOR_FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-3-flash-preview")
