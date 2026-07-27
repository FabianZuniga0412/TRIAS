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
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

configurar_logging()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Esquema de salida
# ---------------------------------------------------------------------------

class CorreccionEnglish(BaseModel):
    assessment: Literal["needs_correction", "correct_but_unnatural", "correct_and_natural"]
    corrected_text: str
    natural_alternative: str = ""
    explanation_es: str
    focus: str
    tts_text: str = Field(min_length=1, max_length=180)


JSON_SCHEMA = CorreccionEnglish.model_json_schema()


# ---------------------------------------------------------------------------
# Prompt del sistema
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are TRIAS, a concise and helpful English tutor for Spanish-speaking learners.

Analyze one English sentence. Teach both correctness and naturalness without changing the learner's intended meaning or inventing context. Give one main learning point only; never give generic praise such as "Great, that was correct".

Choose assessment exactly as follows:
- needs_correction: grammar, word choice, or meaning needs correction.
- correct_but_unnatural: grammatical, but a native speaker would normally phrase it differently in a neutral everyday context.
- correct_and_natural: grammatical and natural. Still provide one specific useful note about usage, register, or why it works.

Return only a JSON object with:
- assessment: one allowed value.
- corrected_text: the best corrected sentence. Preserve the input exactly when it is already correct.
- natural_alternative: a useful natural alternative, or an empty string when no alternative adds value.
- explanation_es: one concise explanation in Spanish; quote English words when useful.
- focus: a short English label, e.g. "Subject-verb agreement", "Natural phrasing", "Verb tense", "Correct and natural".
- tts_text: an English sentence for pronunciation practice. Use natural_alternative when present; otherwise corrected_text. No praise, no questions, and at most 25 words.

Examples:
Input: "She don't like pizza"
Output: {"assessment":"needs_correction","corrected_text":"She doesn't like pizza.","natural_alternative":"","explanation_es":"Con 'she' usamos 'doesn't', no 'don't'.","focus":"Subject-verb agreement","tts_text":"She doesn't like pizza."}

Input: "I want to make a party"
Output: {"assessment":"correct_but_unnatural","corrected_text":"I want to have a party.","natural_alternative":"I want to throw a party.","explanation_es":"Para organizar o celebrar una fiesta, 'have a party' es la opción neutral y natural; 'throw a party' es más informal.","focus":"Natural phrasing","tts_text":"I want to have a party."}

Input: "I went to the store yesterday and bought some milk."
Output: {"assessment":"correct_and_natural","corrected_text":"I went to the store yesterday and bought some milk.","natural_alternative":"","explanation_es":"La oración usa pasado simple de forma natural para una acción terminada ayer.","focus":"Correct and natural","tts_text":"I went to the store yesterday and bought some milk."}
"""


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
                {"role": "user", "content": texto_transcrito},
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
            return CorreccionEnglish(**data)
        except (json.JSONDecodeError, ValidationError) as e:
            ultimo_error = e
            logger.warning(f"Intento {intento}/{max_reintentos} fallo al parsear JSON: {e}")
            logger.warning(f"Contenido recibido: {contenido}")

    # Si tras los reintentos sigue fallando, regresamos un fallback seguro
    # en vez de tronar el pipeline completo.
    logger.error(f"No se pudo obtener JSON valido tras {max_reintentos} intentos. Ultimo error: {ultimo_error}")
    return CorreccionEnglish(
        assessment="needs_correction",
        corrected_text=texto_transcrito,
        natural_alternative="",
        explanation_es="No se pudo analizar la frase en este momento.",
        focus="Analysis unavailable",
        tts_text=texto_transcrito or "Please try again.",
    )


def obtener_respuesta_tts(texto_transcrito: str) -> str:
    """
    Atajo para el pipeline de audio: toma la transcripcion y regresa solo el
    texto corto que TTS debe pronunciar.
    """
    resultado = analizar_texto(texto_transcrito)
    return resultado.tts_text.strip()
