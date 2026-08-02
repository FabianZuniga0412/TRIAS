import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel
from config import MIN_LANGUAGE_CONFIDENCE, WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE
from llm import analizar_texto
from manejo_rutas import configurar_logging, gestor_rutas
from tts import generar_wav

configurar_logging()
logger = logging.getLogger(__name__)

# El modelo se carga una sola vez al importar el modulo.
print(f"Cargando modelo Whisper '{WHISPER_MODEL}' en {WHISPER_DEVICE} ({WHISPER_COMPUTE_TYPE})...")
model = WhisperModel(
    WHISPER_MODEL,
    device=WHISPER_DEVICE,
    compute_type=WHISPER_COMPUTE_TYPE,
)
print("Modelo Whisper listo.")

@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    language_probability: float


def transcribir_audio(wav_path: str) -> TranscriptionResult:
    """
    Recibe la ruta de un WAV y regresa el texto transcrito.
    """
    segments, info = model.transcribe(
        wav_path,
        beam_size=5,
        vad_filter=True,  # Filtra silencios.
    )

    texto = " ".join(segment.text.strip() for segment in segments)
    return TranscriptionResult(texto.strip(), info.language, float(info.language_probability))


def procesar_audio_a_respuesta(
    wav_path: str,
    output_wav_path: str | None = None,
    voice: str = "af_sarah",
    speed: float = 1.0,
    lang: str = "en-us",
) -> dict:
    """
    Pipeline completo:
    1. Transcribe el audio WAV.
    2. Lo manda a llm.py para obtener la correccion.
    3. Convierte la frase corregida a un nuevo WAV con tts.py.
    """
    input_path = Path(wav_path)
    if not input_path.exists():
        raise FileNotFoundError(f"No se encontro el WAV de entrada: {input_path}")

    if output_wav_path is None:
        output_wav_path = str(gestor_rutas.ruta_audio_salida(f"{input_path.stem}_respuesta.wav"))

    logger.info("Procesando audio de entrada: %s", input_path)
    resultado_transcripcion = transcribir_audio(str(input_path))
    transcripcion = resultado_transcripcion.text
    if not transcripcion or resultado_transcripcion.language_probability < MIN_LANGUAGE_CONFIDENCE:
        raise RuntimeError("La transcripcion quedo vacia.")
    if resultado_transcripcion.language != "en":
        raise ValueError("TRIAS solo procesa audios detectados en ingles.")

    analisis = analizar_texto(transcripcion)
    if analisis.input_language != "en":
        raise ValueError("No se pudo confirmar que la entrada esta en ingles.")
    output_wav = generar_wav(
        analisis.corrected_text,
        output_wav_path,
        voice=voice,
        speed=speed,
        lang=lang,
    )
    logger.info("Audio de respuesta generado en: %s", output_wav)

    return {
        "transcription": transcripcion,
        "analysis": analisis.model_dump(),
        "output_wav": output_wav,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pipeline local: WAV -> transcripcion -> LLM -> WAV de respuesta."
    )
    parser.add_argument("input_wav", help="Ruta del archivo WAV de entrada.")
    parser.add_argument(
        "--output-wav",
        dest="output_wav",
        help="Ruta del WAV de salida. Si no se indica, se crea junto al audio de entrada.",
    )
    parser.add_argument("--voice", default="af_sarah", help="Voz para Kokoro TTS.")
    parser.add_argument("--speed", type=float, default=1.0, help="Velocidad de habla.")
    parser.add_argument("--lang", default="en-us", help="Idioma para Kokoro TTS.")
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    resultado = procesar_audio_a_respuesta(
        args.input_wav,
        output_wav_path=args.output_wav,
        voice=args.voice,
        speed=args.speed,
        lang=args.lang,
    )
    print(f"Transcripcion: {resultado['transcription']}")
    print(f"Respuesta TTS: {resultado['analysis']['corrected_text']}")
    print(f"WAV generado: {resultado['output_wav']}")
