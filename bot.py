"""Bot de Telegram: tutor local de inglés para texto y audio."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from audio_utils import (
    convert_to_wav,
    convert_wav_to_ogg,
    download_telegram_file,
    new_input_path,
    new_output_path,
    remove_files,
    run_blocking,
)
from authorization import AuthorizationStore
from config import (
    ACCESS_MODE,
    ADMIN_USER_IDS,
    AUTHORIZATION_STORE_PATH,
    AUTHORIZED_USER_IDS,
    INVITE_CODE,
    MAX_AUDIO_SECONDS,
    MAX_QUEUE_SIZE,
    MIN_LANGUAGE_CONFIDENCE,
    LEARNING_HISTORY_PATH,
    TELEGRAM_BOT_TOKEN,
    TTS_LANG,
    TTS_SPEED,
    TTS_VOICE,
)
from learning_history import LearningHistoryStore
from language_detection import detect_text_language

logger = logging.getLogger(__name__)

NO_ACCESS = "No tienes acceso a este bot."
NON_ENGLISH = "Parece que esta entrada no está en inglés. TRIAS practica inglés: envía una frase o audio en inglés."
INVALID_INVITATION = "La invitación no es válida. Pide a tu profesor o administrador el enlace correcto."
AUDIO_TOO_LONG = "Audio demasiado largo; intenta con uno más corto."
TEXT_TOO_LONG = "Tu texto es demasiado largo. Envía una frase o párrafo corto de hasta 600 caracteres."
ALREADY_PENDING = "Ya tengo una práctica tuya procesándose; espera la respuesta antes de enviar otra."
EMPTY_TRANSCRIPTION = "No pude entender el audio. ¿Puedes repetirlo?"
MAX_TEXT_CHARS = 600


@dataclass(frozen=True)
class LearningJob:
    chat_id: int
    user_id: int
    kind: Literal["audio", "text", "practice"]
    text: str | None = None
    file_id: str | None = None
    filename: str | None = None
    duration_seconds: int = 0
    practice_note: str | None = None


class TelegramAudioBot:
    """Mantiene autorización y una única cola para inferencias locales."""

    def __init__(self, application: Application, authorization_store: AuthorizationStore, history_store: LearningHistoryStore) -> None:
        self.application = application
        self.authorization_store = authorization_store
        self.history_store = history_store
        self.queue: asyncio.Queue[LearningJob] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self.pending_user_ids: set[int] = set()
        self.pending_lock = asyncio.Lock()
        self.worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        self.authorization_store.load()
        self.history_store.load()
        self.worker_task = asyncio.create_task(self._worker(), name="telegram-learning-worker")
        logger.info("Worker de aprendizaje iniciado; capacidad máxima: %s", MAX_QUEUE_SIZE)

    async def stop(self) -> None:
        if self.worker_task is None:
            return
        self.worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.worker_task
        self.worker_task = None

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None:
            return
        if not self.authorization_store.is_authorized(user_id):
            invite_code = context.args[0] if context.args and len(context.args) == 1 else ""
            if not invite_code:
                await update.effective_message.reply_text("Para acceder, envía /access <codigo_de_invitacion>.")
                return
            if not self._is_valid_invitation(invite_code):
                await update.effective_message.reply_text(INVALID_INVITATION if ACCESS_MODE == "invite_code" else NO_ACCESS)
                return
            self.authorization_store.allow(user_id)
        await update.effective_message.reply_text(
            "Acceso autorizado. Envíame una frase escrita o un audio en inglés; "
            "recibirás corrección, naturalidad y pronunciación."
        )

    async def handle_access(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None:
            return
        if self.authorization_store.is_authorized(user_id):
            await update.effective_message.reply_text("Ya tienes acceso a TRIAS.")
            return
        if len(context.args) != 1:
            await update.effective_message.reply_text("Uso: /access <codigo_de_invitacion>")
            return
        if not self._is_valid_invitation(context.args[0]):
            await update.effective_message.reply_text(INVALID_INVITATION if ACCESS_MODE == "invite_code" else NO_ACCESS)
            return
        self.authorization_store.allow(user_id)
        await update.effective_message.reply_text("Acceso autorizado. Ya puedes enviar texto o audio en inglés.")

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None or not self.authorization_store.is_authorized(user_id):
            if update.effective_message:
                await update.effective_message.reply_text(NO_ACCESS)
            return
        message = "Envía una frase escrita o un audio de hasta 60 segundos. Usa /practice para practicar y /progress para ver tu avance."
        if self.authorization_store.is_admin(user_id):
            message += "\n\nAdmin: /allow <user_id>, /revoke <user_id>, /users"
        await update.effective_message.reply_text(message)

    async def handle_practice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None or not self.authorization_store.is_authorized(user_id):
            await update.effective_message.reply_text(NO_ACCESS)
            return
        phrase, note, focus = self.history_store.practice_for(user_id)
        await self._enqueue(update.effective_message, LearningJob(update.effective_chat.id, user_id, "practice", text=phrase, practice_note=note if focus else "Práctica general para comenzar."))

    async def handle_progress(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None or not self.authorization_store.is_authorized(user_id):
            await update.effective_message.reply_text(NO_ACCESS)
            return
        last, top = self.history_store.summary_for(user_id)
        if not top:
            await update.effective_message.reply_text("Aún no tengo errores registrados. Envía una frase para empezar.")
            return
        await update.effective_message.reply_text("Último tema: " + str(last) + "\nTemas a practicar:\n" + "\n".join(f"- {focus}: {count}" for focus, count in top))

    async def handle_allow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        target_id = await self._admin_target_id(update, context)
        if target_id is not None:
            await update.effective_message.reply_text(f"Usuario {target_id} autorizado." if self.authorization_store.allow(target_id) else f"El usuario {target_id} ya tenía acceso.")

    async def handle_revoke(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        target_id = await self._admin_target_id(update, context)
        if target_id is None:
            return
        try:
            changed = self.authorization_store.revoke(target_id)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        await update.effective_message.reply_text(f"Acceso revocado para {target_id}." if changed else f"El usuario {target_id} no tenía acceso.")

    async def handle_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None or not self.authorization_store.is_authorized(user_id):
            await update.effective_message.reply_text(NO_ACCESS)
            return
        if not self.authorization_store.is_admin(user_id):
            await update.effective_message.reply_text("No tienes permisos de administración.")
            return
        await update.effective_message.reply_text("Usuarios autorizados:\n" + "\n".join(map(str, sorted(self.authorization_store.effective_users()))))

    async def handle_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user_id = self._user_id(update)
        if message is None or user_id is None or update.effective_chat is None:
            return
        if not self.authorization_store.is_authorized(user_id):
            await message.reply_text(NO_ACCESS)
            return
        media = message.voice or message.audio
        if media is None:
            return
        duration = int(getattr(media, "duration", 0) or 0)
        if duration > MAX_AUDIO_SECONDS:
            await message.reply_text(AUDIO_TOO_LONG)
            return
        filename = getattr(media, "file_name", None) or ("voice.ogg" if message.voice else "audio")
        await self._enqueue(message, LearningJob(update.effective_chat.id, user_id, "audio", file_id=media.file_id, filename=filename, duration_seconds=duration))

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user_id = self._user_id(update)
        text = (message.text or "").strip() if message else ""
        if message is None or user_id is None or update.effective_chat is None or not text:
            return
        if not self.authorization_store.is_authorized(user_id):
            await message.reply_text(NO_ACCESS)
            return
        if len(text) > MAX_TEXT_CHARS:
            await message.reply_text(TEXT_TOO_LONG)
            return
        await self._enqueue(message, LearningJob(update.effective_chat.id, user_id, "text", text=text))

    async def _enqueue(self, message, job: LearningJob) -> None:
        async with self.pending_lock:
            if job.user_id in self.pending_user_ids:
                await message.reply_text(ALREADY_PENDING)
                return
            if len(self.pending_user_ids) >= MAX_QUEUE_SIZE:
                await message.reply_text("El bot está saturado. Intenta de nuevo en unos minutos.")
                return
            position = len(self.pending_user_ids) + 1
            self.pending_user_ids.add(job.user_id)
            self.queue.put_nowait(job)
        received = "Práctica preparada." if job.kind == "practice" else ("Texto recibido." if job.kind == "text" else "Audio recibido.")
        await message.reply_text(f"{received} Estás en espera; posición en cola: {position}." if position > 1 else f"{received} Estoy procesándolo.")

    async def _admin_target_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
        user_id = self._user_id(update)
        if user_id is None or not self.authorization_store.is_authorized(user_id):
            await update.effective_message.reply_text(NO_ACCESS)
            return None
        if not self.authorization_store.is_admin(user_id):
            await update.effective_message.reply_text("No tienes permisos de administración.")
            return None
        if len(context.args) != 1:
            await update.effective_message.reply_text("Uso: /allow <user_id> o /revoke <user_id>")
            return None
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("El user_id debe ser un número entero.")
            return None
        return target_id if target_id > 0 else None

    @staticmethod
    def _user_id(update: Update) -> int | None:
        return update.effective_user.id if update.effective_user else None

    @staticmethod
    def _is_valid_invitation(invite_code: str) -> bool:
        return ACCESS_MODE == "invite_code" and bool(INVITE_CODE) and hmac.compare_digest(invite_code, INVITE_CODE)

    @staticmethod
    def _run_analysis(text: str) -> dict:
        from llm import analizar_texto

        return analizar_texto(text).model_dump()

    @staticmethod
    def _run_tts(text: str, output_wav: str) -> str:
        from tts import generar_wav

        return generar_wav(text, output_wav, voice=TTS_VOICE, speed=TTS_SPEED, lang=TTS_LANG)

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._process_job(job)
            finally:
                self.queue.task_done()

    async def _process_job(self, job: LearningJob) -> None:
        input_path = wav_path = generated_wav = ogg_path = None
        completed = False
        try:
            text = job.text
            if job.kind == "practice":
                generated_wav = new_output_path(".wav")
                generated_wav = Path(await run_blocking(self._run_tts, text, str(generated_wav)))
                await self.application.bot.send_message(job.chat_id, f"🎯 Práctica: {text}\n💡 {job.practice_note}")
                ogg_path = await run_blocking(convert_wav_to_ogg, generated_wav)
                with ogg_path.open("rb") as audio_file:
                    await self.application.bot.send_voice(chat_id=job.chat_id, voice=audio_file)
                completed = True
                return
            if job.kind == "audio":
                from transcriptor import transcribir_audio

                input_path = new_input_path(job.filename)
                await download_telegram_file(self.application.bot, job.file_id, input_path)
                wav_path = await run_blocking(convert_to_wav, input_path)
                transcription = await run_blocking(transcribir_audio, str(wav_path))
                if not transcription.text or transcription.language_probability < MIN_LANGUAGE_CONFIDENCE:
                    await self.application.bot.send_message(job.chat_id, EMPTY_TRANSCRIPTION)
                    completed = True
                    return
                if transcription.language != "en":
                    await self.application.bot.send_message(job.chat_id, NON_ENGLISH)
                    completed = True
                    return
                text = transcription.text
            elif detect_text_language(text).classification == "other":
                await self.application.bot.send_message(job.chat_id, NON_ENGLISH)
                completed = True
                return
            analysis = await run_blocking(self._run_analysis, text)
            if analysis["input_language"] != "en":
                await self.application.bot.send_message(job.chat_id, NON_ENGLISH)
                completed = True
                return
            if analysis["assessment"] in {"needs_correction", "correct_but_unnatural"}:
                self.history_store.record(job.user_id, analysis["focus"])
            await self.application.bot.send_message(job.chat_id, self._format_feedback(text, analysis, job.kind))
            generated_wav = new_output_path(".wav")
            generated_wav = Path(await run_blocking(self._run_tts, analysis["corrected_text"], str(generated_wav)))
            ogg_path = await run_blocking(convert_wav_to_ogg, generated_wav)
            with ogg_path.open("rb") as audio_file:
                await self.application.bot.send_voice(chat_id=job.chat_id, voice=audio_file)
            completed = True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Fallo procesando %s para usuario=%s", job.kind, job.user_id)
            with contextlib.suppress(Exception):
                await self.application.bot.send_message(job.chat_id, "No pude procesar tu práctica en este momento. Intenta nuevamente más tarde.")
        finally:
            remove_files([input_path, wav_path, generated_wav, ogg_path])
            async with self.pending_lock:
                self.pending_user_ids.discard(job.user_id)
            logger.info("Proceso finalizado usuario=%s tipo=%s resultado=%s", job.user_id, job.kind, "exitoso" if completed else "fallido")

    @staticmethod
    def _format_feedback(received_text: str, analysis: dict, kind: str) -> str:
        lines = []
        if kind == "audio":
            lines.extend([f"📝 Entendí: {received_text}", ""])
        assessment = analysis["assessment"]
        if assessment == "unable_to_analyze":
            lines.append("⚠️ No pude analizar esa entrada de forma segura.")
        elif assessment == "correct_and_natural":
            lines.append("✅ Tu frase es correcta y natural.")
        elif assessment == "correct_but_unnatural":
            lines.append("✅ Tu frase es correcta, pero hay una forma más natural de decirlo.")
        else:
            lines.append(f"✅ Corrección: {analysis['corrected_text']}")
        lines.extend(["", f"💡 Por qué: {analysis['explanation_es']}"])
        if analysis.get("natural_alternative"):
            lines.extend(["", f"🗣️ Más natural: {analysis['natural_alternative']}"])
        lines.append(f"Tema: {analysis['focus']}")
        return "\n".join(lines)


def build_application(token: str = TELEGRAM_BOT_TOKEN) -> Application:
    store = AuthorizationStore(AUTHORIZATION_STORE_PATH, AUTHORIZED_USER_IDS, ADMIN_USER_IDS)
    holder: dict[str, TelegramAudioBot] = {}

    async def post_init(application: Application) -> None:
        service = holder["service"]
        application.bot_data["learning_service"] = service
        await service.start()

    async def post_shutdown(application: Application) -> None:
        await holder["service"].stop()

    application = ApplicationBuilder().token(token).post_init(post_init).post_shutdown(post_shutdown).build()
    service = TelegramAudioBot(application, store, LearningHistoryStore(LEARNING_HISTORY_PATH))
    holder["service"] = service
    application.add_handler(CommandHandler("start", service.handle_start))
    application.add_handler(CommandHandler("access", service.handle_access))
    application.add_handler(CommandHandler("help", service.handle_help))
    application.add_handler(CommandHandler("allow", service.handle_allow))
    application.add_handler(CommandHandler("revoke", service.handle_revoke))
    application.add_handler(CommandHandler("users", service.handle_users))
    application.add_handler(CommandHandler("practice", service.handle_practice))
    application.add_handler(CommandHandler("progress", service.handle_progress))
    application.add_handler(MessageHandler((filters.VOICE | filters.AUDIO) & ~filters.COMMAND, service.handle_audio))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, service.handle_text))
    return application
