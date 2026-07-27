"""Bot de Telegram que orquesta Whisper, Llama y Kokoro en una cola FIFO."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from dataclasses import dataclass
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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
    TELEGRAM_BOT_TOKEN,
    TTS_LANG,
    TTS_SPEED,
    TTS_VOICE,
)

logger = logging.getLogger(__name__)

NO_ACCESS = "No tienes acceso a este bot"
INVALID_INVITATION = "La invitacion no es valida. Pide a tu profesor o administrador el enlace correcto."
AUDIO_TOO_LONG = "Audio demasiado largo, intenta con uno más corto"
ALREADY_PENDING = "Ya tengo un audio tuyo procesando, espera tu respuesta antes de enviar otro"
EMPTY_TRANSCRIPTION = "No pude entender el audio, ¿puedes repetirlo?"


@dataclass(frozen=True)
class AudioJob:
    chat_id: int
    user_id: int
    file_id: str
    filename: str | None
    duration_seconds: int


class TelegramAudioBot:
    """Mantiene el estado de autorizacion y la cola unica de inferencias."""

    def __init__(self, application: Application, authorization_store: AuthorizationStore) -> None:
        self.application = application
        self.authorization_store = authorization_store
        self.queue: asyncio.Queue[AudioJob] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self.pending_user_ids: set[int] = set()
        self.pending_lock = asyncio.Lock()
        self.is_processing = False
        self.worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        self.authorization_store.load()
        self.worker_task = asyncio.create_task(self._worker(), name="telegram-audio-worker")
        logger.info("Worker de audio iniciado; capacidad maxima: %s", MAX_QUEUE_SIZE)

    async def stop(self) -> None:
        if self.worker_task is None:
            return
        self.worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.worker_task
        self.worker_task = None
        logger.info("Worker de audio detenido")

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None:
            return
        if not self.authorization_store.is_authorized(user_id):
            invite_code = context.args[0] if context.args and len(context.args) == 1 else ""
            if not invite_code:
                await update.effective_message.reply_text(
                    "Para acceder, envia /access <codigo_de_invitacion>."
                )
                return
            if not self._is_valid_invitation(invite_code):
                await update.effective_message.reply_text(
                    INVALID_INVITATION if ACCESS_MODE == "invite_code" else NO_ACCESS
                )
                return
            self.authorization_store.allow(user_id)
            await update.effective_message.reply_text("Acceso autorizado. Bienvenido a TRIAS!")
        await update.effective_message.reply_text(
            "Envíame una nota de voz o un archivo de audio en inglés. "
            "Te responderé con una corrección en audio."
        )

    async def handle_access(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Autoriza al usuario con un codigo de invitacion enviado tras /start."""
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
            await update.effective_message.reply_text(
                INVALID_INVITATION if ACCESS_MODE == "invite_code" else NO_ACCESS
            )
            return

        self.authorization_store.allow(user_id)
        await update.effective_message.reply_text(
            "Acceso autorizado. Ya puedes enviar un audio en ingles."
        )

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None:
            return
        if not self.authorization_store.is_authorized(user_id):
            await update.effective_message.reply_text(NO_ACCESS)
            return
        message = "Envía un audio de hasta 60 segundos. Solo se procesa un audio tuyo a la vez."
        if self.authorization_store.is_admin(user_id):
            message += "\n\nAdmin: /allow <user_id>, /revoke <user_id>, /users"
        await update.effective_message.reply_text(message)

    async def handle_allow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        target_id = await self._admin_target_id(update, context)
        if target_id is None:
            return
        changed = self.authorization_store.allow(target_id)
        message = (
            f"Usuario {target_id} autorizado."
            if changed
            else f"El usuario {target_id} ya tenía acceso."
        )
        await update.effective_message.reply_text(message)

    async def handle_revoke(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        target_id = await self._admin_target_id(update, context)
        if target_id is None:
            return
        try:
            changed = self.authorization_store.revoke(target_id)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        message = (
            f"Acceso revocado para {target_id}."
            if changed
            else f"El usuario {target_id} no tenía acceso."
        )
        await update.effective_message.reply_text(message)

    async def handle_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user_id(update)
        if user_id is None:
            return
        if not self.authorization_store.is_authorized(user_id):
            await update.effective_message.reply_text(NO_ACCESS)
            return
        if not self.authorization_store.is_admin(user_id):
            await update.effective_message.reply_text("No tienes permisos de administración.")
            return

        ids = sorted(self.authorization_store.effective_users())
        await update.effective_message.reply_text(
            "Usuarios autorizados:\n" + ("\n".join(map(str, ids)) or "(ninguno)")
        )

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

        filename = getattr(media, "file_name", None)
        if not filename:
            filename = "voice.ogg" if message.voice else "audio"
        job = AudioJob(
            chat_id=update.effective_chat.id,
            user_id=user_id,
            file_id=media.file_id,
            filename=filename,
            duration_seconds=duration,
        )

        async with self.pending_lock:
            if user_id in self.pending_user_ids:
                await message.reply_text(ALREADY_PENDING)
                return
            # Cuenta activo + en cola, de modo que el limite es global y estricto.
            if len(self.pending_user_ids) >= MAX_QUEUE_SIZE:
                await message.reply_text("El bot está saturado. Intenta de nuevo en unos minutos.")
                return

            position = len(self.pending_user_ids) + 1
            self.pending_user_ids.add(user_id)
            self.queue.put_nowait(job)

        if position > 1:
            await message.reply_text(f"Audio recibido. Estás en espera; posición en cola: {position}.")
        else:
            await message.reply_text("Audio recibido. Estoy procesándolo.")

    async def _admin_target_id(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int | None:
        user_id = self._user_id(update)
        if user_id is None:
            return None
        if not self.authorization_store.is_authorized(user_id):
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
        if target_id <= 0:
            await update.effective_message.reply_text("El user_id debe ser un entero positivo.")
            return None
        return target_id

    @staticmethod
    def _user_id(update: Update) -> int | None:
        return update.effective_user.id if update.effective_user else None

    @staticmethod
    def _is_valid_invitation(invite_code: str) -> bool:
        return (
            ACCESS_MODE == "invite_code"
            and bool(INVITE_CODE)
            and hmac.compare_digest(invite_code, INVITE_CODE)
        )

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            self.is_processing = True
            try:
                await self._process_job(job)
            finally:
                self.is_processing = False
                self.queue.task_done()

    @staticmethod
    def _run_pipeline(input_wav: str, output_wav: str) -> str | None:
        """Importa los modelos bajo demanda y procesa un solo audio en un hilo."""
        from llm import analizar_texto
        from transcriptor import transcribir_audio
        from tts import generar_wav

        transcription = transcribir_audio(input_wav)
        if not transcription:
            return None
        analysis = analizar_texto(transcription)
        return generar_wav(
            analysis.natural_response,
            output_wav,
            voice=TTS_VOICE,
            speed=TTS_SPEED,
            lang=TTS_LANG,
        )

    async def _process_job(self, job: AudioJob) -> None:
        input_path: Path | None = None
        wav_path: Path | None = None
        generated_wav: Path | None = None
        ogg_path: Path | None = None
        completed = False

        try:
            input_path = new_input_path(job.filename)
            await download_telegram_file(self.application.bot, job.file_id, input_path)
            wav_path = await run_blocking(convert_to_wav, input_path)
            generated_wav = new_output_path(".wav")
            pipeline_output = await run_blocking(
                self._run_pipeline, str(wav_path), str(generated_wav)
            )

            if pipeline_output is None:
                await self.application.bot.send_message(job.chat_id, EMPTY_TRANSCRIPTION)
                completed = True
                return

            generated_wav = Path(pipeline_output)
            ogg_path = await run_blocking(convert_wav_to_ogg, generated_wav)
            with ogg_path.open("rb") as audio_file:
                await self.application.bot.send_voice(chat_id=job.chat_id, voice=audio_file)
            completed = True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Fallo procesando audio para usuario=%s", job.user_id)
            with contextlib.suppress(Exception):
                await self.application.bot.send_message(
                    job.chat_id,
                    "No pude procesar tu audio en este momento. Intenta nuevamente más tarde.",
                )
        finally:
            remove_files([input_path, wav_path, generated_wav, ogg_path])
            async with self.pending_lock:
                self.pending_user_ids.discard(job.user_id)
            logger.info(
                "Proceso finalizado usuario=%s duracion=%ss resultado=%s",
                job.user_id,
                job.duration_seconds,
                "exitoso" if completed else "fallido",
            )


def build_application(token: str = TELEGRAM_BOT_TOKEN) -> Application:
    """Construye la aplicacion sin iniciar polling; facilita pruebas y despliegue."""
    store = AuthorizationStore(
        path=AUTHORIZATION_STORE_PATH,
        base_users=AUTHORIZED_USER_IDS,
        admins=ADMIN_USER_IDS,
    )
    service_holder: dict[str, TelegramAudioBot] = {}

    async def post_init(application: Application) -> None:
        service = service_holder["service"]
        application.bot_data["audio_service"] = service
        await service.start()

    async def post_shutdown(application: Application) -> None:
        await service_holder["service"].stop()

    application = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    service = TelegramAudioBot(application, store)
    service_holder["service"] = service

    application.add_handler(CommandHandler("start", service.handle_start))
    application.add_handler(CommandHandler("access", service.handle_access))
    application.add_handler(CommandHandler("help", service.handle_help))
    application.add_handler(CommandHandler("allow", service.handle_allow))
    application.add_handler(CommandHandler("revoke", service.handle_revoke))
    application.add_handler(CommandHandler("users", service.handle_users))
    application.add_handler(MessageHandler((filters.VOICE | filters.AUDIO) & ~filters.COMMAND, service.handle_audio))
    return application
