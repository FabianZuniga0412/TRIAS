"""Descarga, conversion y limpieza de archivos temporales de audio."""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path
from typing import Iterable

from manejo_rutas import gestor_rutas


class AudioConversionError(RuntimeError):
    """FFmpeg no pudo completar una conversion solicitada."""


def _safe_suffix(filename: str | None, default: str = ".ogg") -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix and len(suffix) <= 10 else default


def new_input_path(filename: str | None = None) -> Path:
    gestor_rutas.preparar_estructura()
    return gestor_rutas.audio_entrada_dir / f"{uuid.uuid4().hex}{_safe_suffix(filename)}"


def new_output_path(suffix: str) -> Path:
    gestor_rutas.preparar_estructura()
    return gestor_rutas.audio_salida_dir / f"{uuid.uuid4().hex}{suffix}"


async def download_telegram_file(bot, file_id: str, destination: Path) -> Path:
    """Descarga un archivo de Telegram hacia una ruta temporal unica."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    telegram_file = await bot.get_file(file_id)
    await telegram_file.download_to_drive(custom_path=str(destination))
    return destination


def _run_ffmpeg(arguments: list[str]) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AudioConversionError("No se encontro FFmpeg en el PATH.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise AudioConversionError(f"FFmpeg no pudo convertir el audio: {detail}") from exc


def convert_to_wav(source: Path) -> Path:
    """Convierte formatos no WAV a WAV mono de 16 kHz para Faster-Whisper."""
    if source.suffix.lower() == ".wav":
        return source

    destination = new_output_path(".wav")
    _run_ffmpeg(["-i", str(source), "-ac", "1", "-ar", "16000", str(destination)])
    return destination


def convert_wav_to_ogg(source: Path) -> Path:
    """Convierte el WAV generado por Kokoro a una nota de voz OGG/Opus."""
    destination = new_output_path(".ogg")
    _run_ffmpeg(
        ["-i", str(source), "-c:a", "libopus", "-b:a", "48k", "-application", "voip", str(destination)]
    )
    return destination


async def run_blocking(function, *args, **kwargs):
    """Ejecuta conversiones o inferencia sin bloquear el event loop de Telegram."""
    return await asyncio.to_thread(function, *args, **kwargs)


def remove_files(paths: Iterable[Path | str | None]) -> None:
    """Elimina archivos temporales sin ocultar el error principal del flujo."""
    for path in paths:
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            # La limpieza no debe impedir que el usuario reciba una respuesta/error.
            pass
