import asyncio
from pathlib import Path
from types import SimpleNamespace

import bot
from authorization import AuthorizationStore


class FakeMessage:
    def __init__(self, voice=None, audio=None):
        self.voice = voice
        self.audio = audio
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeBot:
    def __init__(self):
        self.messages = []
        self.voices = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def send_voice(self, chat_id, voice):
        self.voices.append((chat_id, voice.read()))


def make_update(user_id, message):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=777),
    )


def make_service(tmp_path):
    application = SimpleNamespace(bot=FakeBot())
    store = AuthorizationStore(tmp_path / "users.json", base_users=frozenset({1}))
    store.load()
    return bot.TelegramAudioBot(application, store)


def test_unauthorized_audio_is_rejected_before_download(tmp_path):
    service = make_service(tmp_path)
    message = FakeMessage(voice=SimpleNamespace(file_id="f", duration=10))

    asyncio.run(service.handle_audio(make_update(2, message), None))

    assert message.replies == [bot.NO_ACCESS]
    assert service.queue.empty()


def test_start_with_valid_invitation_authorizes_user(monkeypatch, tmp_path):
    service = make_service(tmp_path)
    message = FakeMessage()
    context = SimpleNamespace(args=["TRIAS2026"])
    monkeypatch.setattr(bot, "ACCESS_MODE", "invite_code")
    monkeypatch.setattr(bot, "INVITE_CODE", "TRIAS2026")

    asyncio.run(service.handle_start(make_update(2, message), context))

    assert service.authorization_store.is_authorized(2)
    assert message.replies[0] == "Acceso autorizado. Bienvenido a TRIAS!"


def test_start_with_invalid_invitation_does_not_authorize_user(monkeypatch, tmp_path):
    service = make_service(tmp_path)
    message = FakeMessage()
    context = SimpleNamespace(args=["incorrecta"])
    monkeypatch.setattr(bot, "ACCESS_MODE", "invite_code")
    monkeypatch.setattr(bot, "INVITE_CODE", "TRIAS2026")

    asyncio.run(service.handle_start(make_update(2, message), context))

    assert not service.authorization_store.is_authorized(2)
    assert message.replies == [bot.INVALID_INVITATION]


def test_access_command_authorizes_user_after_start(monkeypatch, tmp_path):
    service = make_service(tmp_path)
    message = FakeMessage()
    context = SimpleNamespace(args=["TRIAS2026"])
    monkeypatch.setattr(bot, "ACCESS_MODE", "invite_code")
    monkeypatch.setattr(bot, "INVITE_CODE", "TRIAS2026")

    asyncio.run(service.handle_access(make_update(2, message), context))

    assert service.authorization_store.is_authorized(2)
    assert message.replies == ["Acceso autorizado. Ya puedes enviar un audio en ingles."]


def test_start_without_code_explains_how_to_access(monkeypatch, tmp_path):
    service = make_service(tmp_path)
    message = FakeMessage()
    monkeypatch.setattr(bot, "ACCESS_MODE", "invite_code")

    asyncio.run(service.handle_start(make_update(2, message), SimpleNamespace(args=[])))

    assert message.replies == ["Para acceder, envia /access <codigo_de_invitacion>."]


def test_long_and_duplicate_audio_are_rejected(tmp_path):
    service = make_service(tmp_path)
    long_message = FakeMessage(voice=SimpleNamespace(file_id="long", duration=61))
    asyncio.run(service.handle_audio(make_update(1, long_message), None))
    assert long_message.replies == [bot.AUDIO_TOO_LONG]

    first = FakeMessage(voice=SimpleNamespace(file_id="one", duration=10))
    second = FakeMessage(voice=SimpleNamespace(file_id="two", duration=10))
    asyncio.run(service.handle_audio(make_update(1, first), None))
    asyncio.run(service.handle_audio(make_update(1, second), None))
    assert first.replies == ["Audio recibido. Estoy procesándolo."]
    assert second.replies == [bot.ALREADY_PENDING]


def test_second_user_receives_fifo_queue_position(tmp_path):
    service = make_service(tmp_path)
    first = FakeMessage(voice=SimpleNamespace(file_id="one", duration=10))
    second = FakeMessage(voice=SimpleNamespace(file_id="two", duration=10))
    service.authorization_store.allow(2)

    asyncio.run(service.handle_audio(make_update(1, first), None))
    asyncio.run(service.handle_audio(make_update(2, second), None))

    assert second.replies == ["Audio recibido. Estás en espera; posición en cola: 2."]


def test_successful_job_sends_ogg_and_removes_temporaries(monkeypatch, tmp_path):
    service = make_service(tmp_path)
    input_path = tmp_path / "input.ogg"
    generated_wav = tmp_path / "generated.wav"
    reply_ogg = tmp_path / "reply.ogg"
    output_paths = iter([generated_wav])

    monkeypatch.setattr(bot, "new_input_path", lambda filename: input_path)
    monkeypatch.setattr(bot, "new_output_path", lambda suffix: next(output_paths))

    async def fake_download(telegram_bot, file_id, destination):
        destination.write_bytes(b"input")
        return destination

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "convert_to_wav":
            return Path(args[0])
        if function.__name__ == "_run_pipeline":
            Path(args[1]).write_bytes(b"wav")
            return args[1]
        if function.__name__ == "convert_wav_to_ogg":
            reply_ogg.write_bytes(b"ogg")
            return reply_ogg
        raise AssertionError(f"Funcion inesperada: {function}")

    monkeypatch.setattr(bot, "download_telegram_file", fake_download)
    monkeypatch.setattr(bot, "run_blocking", fake_run)

    job = bot.AudioJob(777, 1, "file", "voice.ogg", 10)
    asyncio.run(service._process_job(job))

    assert service.application.bot.voices == [(777, b"ogg")]
    assert not input_path.exists()
    assert not generated_wav.exists()
    assert not reply_ogg.exists()


def test_empty_transcription_requests_resend(monkeypatch, tmp_path):
    service = make_service(tmp_path)
    input_path = tmp_path / "input.wav"
    generated_wav = tmp_path / "generated.wav"
    monkeypatch.setattr(bot, "new_input_path", lambda filename: input_path)
    monkeypatch.setattr(bot, "new_output_path", lambda suffix: generated_wav)

    async def fake_download(telegram_bot, file_id, destination):
        destination.write_bytes(b"input")
        return destination

    async def fake_run(function, *args, **kwargs):
        if function.__name__ == "convert_to_wav":
            return Path(args[0])
        if function.__name__ == "_run_pipeline":
            return None
        raise AssertionError(f"Funcion inesperada: {function}")

    monkeypatch.setattr(bot, "download_telegram_file", fake_download)
    monkeypatch.setattr(bot, "run_blocking", fake_run)

    asyncio.run(service._process_job(bot.AudioJob(777, 1, "file", "voice.wav", 10)))

    assert service.application.bot.messages == [(777, bot.EMPTY_TRANSCRIPTION)]
    assert not input_path.exists()


def test_pipeline_failure_is_logged_and_reported(monkeypatch, tmp_path, caplog):
    service = make_service(tmp_path)
    input_path = tmp_path / "input.wav"
    monkeypatch.setattr(bot, "new_input_path", lambda filename: input_path)

    async def fake_download(telegram_bot, file_id, destination):
        destination.write_bytes(b"input")
        return destination

    async def failing_run(function, *args, **kwargs):
        raise RuntimeError("modelo no disponible")

    monkeypatch.setattr(bot, "download_telegram_file", fake_download)
    monkeypatch.setattr(bot, "run_blocking", failing_run)

    with caplog.at_level("INFO"):
        asyncio.run(service._process_job(bot.AudioJob(777, 1, "file", "voice.wav", 10)))

    assert service.application.bot.messages
    assert "resultado=fallido" in caplog.text
