"""Fake adapters package."""

from app.adapters.fake.speech import FakeSpeechBackend
from app.adapters.fake.translator import FakeTranslator

__all__ = ["FakeSpeechBackend", "FakeTranslator"]
