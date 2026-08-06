"""
llm.py - Modulo de correccion/analisis de ingles usando Llama local (llama-cpp-python)

Recibe la transcripcion cruda de FasterWhisper y regresa un objeto estructurado
con la correccion, explicacion, tema gramatical y una respuesta breve para TTS (Kokoro).
"""

import json
import logging
from pathlib import Path

from config import LLAMA_MODEL_PATH, LLAMA_GPU_LAYERS, LLAMA_CTX, LLAMA_MODEL_URL
from manejo_rutas import ModeloRemoto, configurar_logging, configurar_rutas_cuda_windows, gestor_rutas

configurar_rutas_cuda_windows()

from llama_cpp import Llama
from pydantic import ValidationError
from tutor_contract import CorreccionEnglish, SYSTEM_PROMPT, align_analysis_with_assessment, learner_message

configurar_logging()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Esquema de salida
# ---------------------------------------------------------------------------

JSON_SCHEMA = CorreccionEnglish.model_json_schema()


# ---------------------------------------------------------------------------
# Prompt del sistema
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Carga del modelo (una sola vez, al importar el modulo)
# ---------------------------------------------------------------------------

logger.info(f"Cargando modelo Llama desde {LLAMA_MODEL_PATH} (GPU layers: {LLAMA_GPU_LAYERS})...")
modelo_llama = ModeloRemoto(
    nombre="llama-gguf",
    ruta_local=Path(LLAMA_MODEL_PATH),
    url_descarga=LLAMA_MODEL_URL,
)
modelo_llama_path = gestor_rutas.asegurar_modelo(modelo_llama)
llm = Llama(
    model_path=str(modelo_llama_path),
    n_gpu_layers=LLAMA_GPU_LAYERS,
    n_ctx=LLAMA_CTX,
    verbose=False,
)
logger.info("Modelo Llama listo.")


# ---------------------------------------------------------------------------
# Funcion principal
# ---------------------------------------------------------------------------

def analizar_texto(texto_transcrito: str, max_reintentos: int = 2) -> CorreccionEnglish:
    """
    Recibe la transcripcion cruda de Whisper y regresa un objeto CorreccionEnglish
    con la correccion, explicacion, tema gramatical y respuesta corta para TTS.

    Reintenta si el JSON no cumple el esquema esperado (poco comun gracias al
    modo de gramatica forzada, pero se deja como respaldo).
    """
    ultimo_error = None

    for intento in range(1, max_reintentos + 1):
        respuesta = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": learner_message(texto_transcrito)},
            ],
            temperature=0.3,
            max_tokens=350,
            response_format={
                "type": "json_object",
                "schema": JSON_SCHEMA,
            },
        )

        contenido = respuesta["choices"][0]["message"]["content"]

        try:
            data = json.loads(contenido)
            return align_analysis_with_assessment(CorreccionEnglish(**data), texto_transcrito)
        except (json.JSONDecodeError, ValidationError) as e:
            ultimo_error = e
            logger.warning(f"Intento {intento}/{max_reintentos} fallo al parsear JSON: {e}")
            logger.warning(f"Contenido recibido: {contenido}")

    # Si tras los reintentos sigue fallando, regresamos un fallback seguro
    # en vez de tronar el pipeline completo.
    logger.error(f"No se pudo obtener JSON valido tras {max_reintentos} intentos. Ultimo error: {ultimo_error}")
    return CorreccionEnglish(
        input_language="uncertain",
        assessment="unable_to_analyze",
        corrected_text="Please send a short English sentence.",
        natural_alternative="",
        explanation_es="No pude analizar esa entrada de forma segura. Envía una frase corta para practicar inglés.",
        focus="Sentence structure",
    )


def obtener_respuesta_tts(texto_transcrito: str) -> str:
    """
    Atajo para el pipeline de audio: toma la transcripcion y regresa solo el
    texto corto que TTS debe pronunciar.
    """
    resultado = analizar_texto(texto_transcrito)
    return resultado.corrected_text.strip()
