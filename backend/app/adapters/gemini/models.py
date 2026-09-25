"""Gemini model id strings — ONLY place to change if names fail at runtime."""

SPEECH_MODEL = "gemini-3.5-transcribe-live"
TRANSLATOR_MODEL = "gemini-3.5-flash"

# Interims only. Lite answered in ~1 s in the 24 sep probe; the primary model often
# returns after the utterance already closed, so the live line never appears.
INTERIM_TRANSLATOR_MODEL = "gemini-3.5-flash-lite"

# Finals only, in order, when TRANSLATOR_MODEL answers 429/5xx.
# Interims never use this chain: see GeminiTranslator._pick_model.
TRANSLATOR_FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-3-flash-preview")
