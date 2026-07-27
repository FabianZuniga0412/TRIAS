import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

_llama_model_path = Path(
    os.getenv("LLAMA_MODEL_PATH", "./models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf")
)
LLAMA_MODEL_PATH = str(
    _llama_model_path.resolve()
    if _llama_model_path.is_absolute()
    else (BASE_DIR / _llama_model_path).resolve()
)
LLAMA_MODEL_URL = os.getenv("LLAMA_MODEL_URL", "").strip() or None
LLAMA_GPU_LAYERS = int(os.getenv("LLAMA_GPU_LAYERS", "-1"))
LLAMA_CTX = int(os.getenv("LLAMA_CTX", "4096"))
TTS_ONNX_PROVIDER = os.getenv("TTS_ONNX_PROVIDER", "CUDAExecutionProvider")


def _int_env(name: str, default: int) -> int:
    """Lee un entero de entorno con un error claro si el valor es invalido."""
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un numero entero, no {raw_value!r}.") from exc


def parse_user_ids(value: str | None) -> frozenset[int]:
    """Convierte una lista CSV de IDs de Telegram a un conjunto de enteros."""
    if not value or not value.strip():
        return frozenset()

    ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError as exc:
            raise ValueError(
                "Los IDs de Telegram deben ser enteros separados por comas. "
                f"Valor invalido: {item!r}."
            ) from exc
    return frozenset(ids)


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
AUTHORIZED_USER_IDS = parse_user_ids(os.getenv("AUTHORIZED_USER_IDS"))
ADMIN_USER_IDS = parse_user_ids(os.getenv("ADMIN_USER_IDS"))
ACCESS_MODE = os.getenv("ACCESS_MODE", "invite_code").strip().lower()
INVITE_CODE = os.getenv("INVITE_CODE", "").strip()
MAX_AUDIO_SECONDS = _int_env("MAX_AUDIO_SECONDS", 60)
MAX_QUEUE_SIZE = _int_env("MAX_QUEUE_SIZE", 10)
TTS_VOICE = os.getenv("TTS_VOICE", "af_sarah").strip() or "af_sarah"
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))
TTS_LANG = os.getenv("TTS_LANG", "en-us").strip() or "en-us"

AUTHORIZATION_STORE_PATH = BASE_DIR / "data" / "authorized_users.json"


def validate_bot_config() -> None:
    """Valida los valores necesarios para iniciar el bot sin exponer secretos."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en el archivo .env.")
    if ACCESS_MODE not in {"invite_code", "closed"}:
        raise RuntimeError("ACCESS_MODE debe ser 'invite_code' o 'closed'.")
    if ACCESS_MODE == "invite_code" and not INVITE_CODE:
        raise RuntimeError("Falta INVITE_CODE para el modo invite_code.")
    if MAX_AUDIO_SECONDS <= 0:
        raise RuntimeError("MAX_AUDIO_SECONDS debe ser mayor que cero.")
    if MAX_QUEUE_SIZE <= 0:
        raise RuntimeError("MAX_QUEUE_SIZE debe ser mayor que cero.")
    if TTS_SPEED <= 0:
        raise RuntimeError("TTS_SPEED debe ser mayor que cero.")
