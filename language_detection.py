"""Detección local y conservadora de idioma para mensajes de texto."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

from config import MIN_TEXT_LANGUAGE_CONFIDENCE


TextLanguage = Literal["en", "other", "uncertain"]


@dataclass(frozen=True)
class TextLanguageResult:
    classification: TextLanguage
    detected_language: str | None
    confidence: float


# Este primer detector diferencia específicamente español e inglés. Es útil
# para frases cortas, que pueden ser ambiguas cuando se comparan demasiados
# idiomas a la vez.
_spanish_english_detector: LanguageDetector = LanguageDetectorBuilder.from_languages(
    Language.ENGLISH,
    Language.SPANISH,
).build()

# El segundo detector cubre otros idiomas frecuentes sin cargar modelos de
# todos los idiomas disponibles. Solo bloquea cuando la señal es clara.
_common_language_detector: LanguageDetector = LanguageDetectorBuilder.from_languages(
    Language.ENGLISH,
    Language.SPANISH,
    Language.PORTUGUESE,
    Language.FRENCH,
    Language.GERMAN,
    Language.ITALIAN,
    Language.DUTCH,
    Language.RUSSIAN,
    Language.UKRAINIAN,
    Language.ARABIC,
    Language.JAPANESE,
    Language.KOREAN,
    Language.CHINESE,
    Language.HINDI,
).build()


def _best_language(detector: LanguageDetector, text: str) -> tuple[Language | None, float]:
    values = detector.compute_language_confidence_values(text)
    if not values:
        return None, 0.0
    return values[0].language, float(values[0].value)


def detect_text_language(text: str) -> TextLanguageResult:
    """Clasifica texto antes de llamar al LLM sin rechazar frases ambiguas."""
    cleaned_text = text.strip()
    if len(cleaned_text) < 3:
        return TextLanguageResult("uncertain", None, 0.0)

    detected, confidence = _best_language(_spanish_english_detector, cleaned_text)
    if detected is Language.SPANISH and confidence >= MIN_TEXT_LANGUAGE_CONFIDENCE:
        return TextLanguageResult("other", "es", confidence)

    detected, confidence = _best_language(_common_language_detector, cleaned_text)
    if detected is not None and detected is not Language.ENGLISH and confidence >= MIN_TEXT_LANGUAGE_CONFIDENCE:
        return TextLanguageResult("other", detected.iso_code_639_1.name.lower(), confidence)
    if detected is Language.ENGLISH and confidence >= MIN_TEXT_LANGUAGE_CONFIDENCE:
        return TextLanguageResult("en", "en", confidence)
    return TextLanguageResult("uncertain", detected.iso_code_639_1.name.lower() if detected else None, confidence)
