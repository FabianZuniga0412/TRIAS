import asyncio
from pathlib import Path
from types import SimpleNamespace

import bot
from authorization import AuthorizationStore
from learning_history import LearningHistoryStore


class FakeMessage:
    def __init__(self, voice=None, audio=None, text=None):
        self.voice, self.audio, self.text = voice, audio, text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeBot:
    def __init__(self):
        self.messages, self.voices = [], []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def send_voice(self, chat_id, voice):
        self.voices.append((chat_id, voice.read()))


def update(user_id, message):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=message, effective_chat=SimpleNamespace(id=777))


def service(tmp_path):
    store = AuthorizationStore(tmp_path / "users.json", base_users=frozenset({1}))
    store.load()
    return bot.TelegramAudioBot(SimpleNamespace(bot=FakeBot()), store, LearningHistoryStore(tmp_path / "history.json"))


def analysis():
    return {"input_language": "en", "assessment": "needs_correction", "corrected_text": "She doesn't like pizza.", "natural_alternative": "", "explanation_es": "Con 'she' usamos 'doesn't', no 'don't'.", "focus": "Subject-verb agreement"}


def test_unauthorized_audio_is_rejected(tmp_path):
    app = service(tmp_path)
    message = FakeMessage(voice=SimpleNamespace(file_id="f", duration=10))
    asyncio.run(app.handle_audio(update(2, message), None))
    assert message.replies == [bot.NO_ACCESS]
    assert app.queue.empty()


def test_text_is_queued_and_shares_duplicate_guard(tmp_path):
    app = service(tmp_path)
    text = FakeMessage(text="She don't like pizza")
    audio = FakeMessage(voice=SimpleNamespace(file_id="f", duration=10))
    asyncio.run(app.handle_text(update(1, text), None))
    asyncio.run(app.handle_audio(update(1, audio), None))
    assert text.replies == ["Texto recibido. Estoy procesándolo."]
    assert audio.replies == [bot.ALREADY_PENDING]
    assert app.queue.get_nowait().kind == "text"


def test_text_length_and_access_are_validated(tmp_path):
    app = service(tmp_path)
    message = FakeMessage(text="x" * (bot.MAX_TEXT_CHARS + 1))
    asyncio.run(app.handle_text(update(1, message), None))
    assert message.replies == [bot.TEXT_TOO_LONG]


def test_text_with_more_than_three_sentences_is_rejected(tmp_path):
    app = service(tmp_path)
    message = FakeMessage(text="First sentence. Second sentence. Third sentence. Fourth sentence.")
    asyncio.run(app.handle_text(update(1, message), None))
    assert message.replies == [bot.TOO_MANY_SENTENCES]
    assert app.queue.empty()


def test_text_job_sends_feedback_before_voice_and_cleans_files(monkeypatch, tmp_path):
    app = service(tmp_path)
    wav, ogg = tmp_path / "reply.wav", tmp_path / "reply.ogg"
    monkeypatch.setattr(bot, "new_output_path", lambda suffix: wav)

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "_run_analysis":
            return analysis()
        if function.__name__ == "_run_tts":
            wav.write_bytes(b"wav")
            return str(wav)
        if function.__name__ == "convert_wav_to_ogg":
            ogg.write_bytes(b"ogg")
            return ogg
        raise AssertionError(function.__name__)

    monkeypatch.setattr(bot, "run_blocking", fake_run)
    asyncio.run(app._process_job(bot.LearningJob(777, 1, "text", text="She don't like pizza")))
    assert "✅ Corrección: She doesn't like pizza." in app.application.bot.messages[0][1]
    assert app.application.bot.voices == [(777, b"ogg")]
    assert not wav.exists() and not ogg.exists()


def test_audio_job_includes_transcription_feedback_and_voice(monkeypatch, tmp_path):
    app = service(tmp_path)
    input_path, wav, ogg = tmp_path / "input.ogg", tmp_path / "reply.wav", tmp_path / "reply.ogg"
    monkeypatch.setattr(bot, "new_input_path", lambda filename: input_path)
    monkeypatch.setattr(bot, "new_output_path", lambda suffix: wav)

    async def download(_, __, destination):
        destination.write_bytes(b"input")
        return destination

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "convert_to_wav":
            return Path(args[0])
        if function.__name__ == "transcribir_audio":
            return SimpleNamespace(text="She don't like pizza", language="en", language_probability=0.95)
        if function.__name__ == "_run_analysis":
            return analysis()
        if function.__name__ == "_run_tts":
            wav.write_bytes(b"wav")
            return str(wav)
        if function.__name__ == "convert_wav_to_ogg":
            ogg.write_bytes(b"ogg")
            return ogg
        raise AssertionError(function.__name__)

    monkeypatch.setattr(bot, "download_telegram_file", download)
    monkeypatch.setattr(bot, "run_blocking", fake_run)
    asyncio.run(app._process_job(bot.LearningJob(777, 1, "audio", file_id="f", filename="voice.ogg")))
    assert app.application.bot.messages[0][1].startswith("📝 Entendí: She don't like pizza")
    assert app.application.bot.voices == [(777, b"ogg")]
    assert not input_path.exists() and not wav.exists() and not ogg.exists()


def test_feedback_formats_natural_sentence_without_generic_praise():
    result = {"input_language": "en", "assessment": "correct_and_natural", "corrected_text": "I went home.", "natural_alternative": "", "explanation_es": "Usa pasado simple para una acción terminada.", "focus": "Correct and natural"}
    text = bot.TelegramAudioBot._format_feedback("I went home.", result, "text")
    assert "✅ Tu frase es correcta y natural." in text
    assert "Great" not in text


def test_tts_uses_only_the_visible_natural_alternative_when_available():
    result = analysis() | {
        "assessment": "correct_but_unnatural",
        "corrected_text": "I have 20 years.",
        "natural_alternative": "I am 20 years old.",
        "focus": "Natural phrasing",
    }
    assert bot.TelegramAudioBot._tts_practice_text(result) == "I am 20 years old."


def test_tts_preserves_correct_and_natural_text():
    result = analysis() | {
        "assessment": "correct_and_natural",
        "corrected_text": "How long did you even cry for me?",
        "natural_alternative": "",
        "focus": "Correct and natural",
    }
    assert bot.TelegramAudioBot._tts_practice_text(result) == "How long did you even cry for me?"


def test_text_in_other_language_does_not_reach_tts(monkeypatch, tmp_path):
    app = service(tmp_path)

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "_run_analysis":
            return analysis() | {"input_language": "other", "assessment": "unable_to_analyze"}
        raise AssertionError(f"No debe ejecutarse {function.__name__}")

    monkeypatch.setattr(bot, "run_blocking", fake_run)
    asyncio.run(app._process_job(bot.LearningJob(777, 1, "text", text="Hola, ¿cómo estás?")))
    assert app.application.bot.messages == [(777, bot.NON_ENGLISH)]
    assert app.application.bot.voices == []


def test_audio_with_non_english_stops_before_analysis(monkeypatch, tmp_path):
    app = service(tmp_path)
    input_path = tmp_path / "input.ogg"
    monkeypatch.setattr(bot, "new_input_path", lambda filename: input_path)

    async def download(_, __, destination):
        destination.write_bytes(b"input")
        return destination

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "convert_to_wav":
            return Path(args[0])
        if function.__name__ == "transcribir_audio":
            return SimpleNamespace(text="Hola hermano", language="es", language_probability=0.98)
        raise AssertionError(f"No debe ejecutarse {function.__name__}")

    monkeypatch.setattr(bot, "download_telegram_file", download)
    monkeypatch.setattr(bot, "run_blocking", fake_run)
    asyncio.run(app._process_job(bot.LearningJob(777, 1, "audio", file_id="f", filename="voice.ogg")))
    assert app.application.bot.messages == [(777, bot.NON_ENGLISH)]
    assert app.application.bot.voices == []
    assert not input_path.exists()


def test_audio_with_wrong_whisper_language_but_english_transcript_continues(monkeypatch, tmp_path):
    app = service(tmp_path)
    input_path, wav, ogg = tmp_path / "input.ogg", tmp_path / "reply.wav", tmp_path / "reply.ogg"
    monkeypatch.setattr(bot, "new_input_path", lambda filename: input_path)
    monkeypatch.setattr(bot, "new_output_path", lambda suffix: wav)

    async def download(_, __, destination):
        destination.write_bytes(b"input")
        return destination

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "convert_to_wav":
            return Path(args[0])
        if function.__name__ == "transcribir_audio":
            return SimpleNamespace(text="She dont like pizza.", language="es", language_probability=0.98)
        if function.__name__ == "_run_analysis":
            return analysis()
        if function.__name__ == "_run_tts":
            wav.write_bytes(b"wav")
            return str(wav)
        if function.__name__ == "convert_wav_to_ogg":
            ogg.write_bytes(b"ogg")
            return ogg
        raise AssertionError(function.__name__)

    monkeypatch.setattr(bot, "download_telegram_file", download)
    monkeypatch.setattr(bot, "run_blocking", fake_run)
    asyncio.run(app._process_job(bot.LearningJob(777, 1, "audio", file_id="f", filename="voice.ogg")))
    assert "She doesn't like pizza." in app.application.bot.messages[0][1]
    assert app.application.bot.voices == [(777, b"ogg")]


def test_audio_with_low_confidence_asks_to_repeat(monkeypatch, tmp_path):
    app = service(tmp_path)
    input_path = tmp_path / "input.ogg"
    monkeypatch.setattr(bot, "new_input_path", lambda filename: input_path)

    async def download(_, __, destination):
        destination.write_bytes(b"input")
        return destination

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "convert_to_wav":
            return Path(args[0])
        if function.__name__ == "transcribir_audio":
            return SimpleNamespace(text="Hola hermano", language="es", language_probability=0.20)
        raise AssertionError(f"No debe ejecutarse {function.__name__}")

    monkeypatch.setattr(bot, "download_telegram_file", download)
    monkeypatch.setattr(bot, "run_blocking", fake_run)
    asyncio.run(app._process_job(bot.LearningJob(777, 1, "audio", file_id="f", filename="voice.ogg")))
    assert app.application.bot.messages == [(777, bot.EMPTY_TRANSCRIPTION)]
    assert app.application.bot.voices == []


def test_audio_with_low_whisper_confidence_and_english_transcript_continues(monkeypatch, tmp_path):
    app = service(tmp_path)
    input_path, wav, ogg = tmp_path / "input.ogg", tmp_path / "reply.wav", tmp_path / "reply.ogg"
    monkeypatch.setattr(bot, "new_input_path", lambda filename: input_path)
    monkeypatch.setattr(bot, "new_output_path", lambda suffix: wav)

    async def download(_, __, destination):
        destination.write_bytes(b"input")
        return destination

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "convert_to_wav":
            return Path(args[0])
        if function.__name__ == "transcribir_audio":
            return SimpleNamespace(text="She dont like pizza.", language="en", language_probability=0.46)
        if function.__name__ == "_run_analysis":
            return analysis()
        if function.__name__ == "_run_tts":
            wav.write_bytes(b"wav")
            return str(wav)
        if function.__name__ == "convert_wav_to_ogg":
            ogg.write_bytes(b"ogg")
            return ogg
        raise AssertionError(function.__name__)

    monkeypatch.setattr(bot, "download_telegram_file", download)
    monkeypatch.setattr(bot, "run_blocking", fake_run)
    asyncio.run(app._process_job(bot.LearningJob(777, 1, "audio", file_id="f", filename="voice.ogg")))
    assert "She doesn't like pizza." in app.application.bot.messages[0][1]
    assert app.application.bot.voices == [(777, b"ogg")]


def test_practice_delivers_last_focus_phrase_and_voice(monkeypatch, tmp_path):
    app = service(tmp_path)
    app.history_store.record(1, "Subject-verb agreement")
    wav, ogg = tmp_path / "practice.wav", tmp_path / "practice.ogg"
    monkeypatch.setattr(bot, "new_output_path", lambda suffix: wav)

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "_run_tts":
            assert args[0] == "She works every day."
            wav.write_bytes(b"wav")
            return str(wav)
        if function.__name__ == "convert_wav_to_ogg":
            ogg.write_bytes(b"ogg")
            return ogg
        raise AssertionError(function.__name__)

    monkeypatch.setattr(bot, "run_blocking", fake_run)
    asyncio.run(app._process_job(bot.LearningJob(777, 1, "practice", text="She works every day.", practice_note="Con he, she e it, el verbo suele llevar -s en presente.")))
    assert "🎯 Práctica: She works every day." in app.application.bot.messages[0][1]
    assert app.application.bot.voices == [(777, b"ogg")]
    assert not wav.exists() and not ogg.exists()


def test_progress_reports_last_focus_and_top_topics(tmp_path):
    app = service(tmp_path)
    app.history_store.record(1, "Articles")
    app.history_store.record(1, "Verb tense")
    app.history_store.record(1, "Articles")
    message = FakeMessage()
    asyncio.run(app.handle_progress(update(1, message), None))
    assert message.replies == ["Último tema: Articles\nTemas a practicar:\n- Articles: 2\n- Verb tense: 1"]
