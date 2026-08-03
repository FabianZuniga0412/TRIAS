from language_detection import detect_text_language


def test_clearly_spanish_text_is_blocked_before_llm():
    result = detect_text_language("Hola, como estás mi hermano?")
    assert result.classification == "other"
    assert result.detected_language == "es"
    assert result.confidence >= 0.70


def test_english_sentence_is_not_classified_as_other():
    result = detect_text_language("She does not like pizza.")
    assert result.classification in {"en", "uncertain"}


def test_very_short_ambiguous_text_is_left_for_the_tutor_contract():
    result = detect_text_language("No")
    assert result.classification == "uncertain"
