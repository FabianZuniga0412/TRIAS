import asyncio
from pathlib import Path
from types import SimpleNamespace

import bot
from authorization import AuthorizationStore


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
    return bot.TelegramAudioBot(SimpleNamespace(bot=FakeBot()), store)


def analysis():
    return {"assessment": "needs_correction", "corrected_text": "She doesn't like pizza.", "natural_alternative": "", "explanation_es": "Con 'she' usamos 'doesn't', no 'don't'.", "focus": "Subject-verb agreement", "tts_text": "She doesn't like pizza."}


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


def test_text_job_sends_feedback_before_voice_and_cleans_files(monkeypatch, tmp_path):
    app = service(tmp_path)
    wav, ogg = tmp_path / "reply.wav", tmp_path / "reply.ogg"
    monkeypatch.setattr(bot, "new_output_path", lambda suffix: wav)

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "_run_analysis":
            wav.write_bytes(b"wav")
            return {"analysis": analysis(), "output_wav": str(wav)}
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
            return "She don't like pizza"
        if function.__name__ == "_run_analysis":
            wav.write_bytes(b"wav")
            return {"analysis": analysis(), "output_wav": str(wav)}
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
    result = {"assessment": "correct_and_natural", "corrected_text": "I went home.", "natural_alternative": "", "explanation_es": "Usa pasado simple para una acción terminada.", "focus": "Correct and natural", "tts_text": "I went home."}
    text = bot.TelegramAudioBot._format_feedback("I went home.", result, "text")
    assert "✅ Tu frase es correcta y natural." in text
    assert "Great" not in text
