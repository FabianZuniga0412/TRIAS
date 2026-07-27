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
from pydantic import BaseModel, ValidationError

configurar_logging()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Esquema de salida
# ---------------------------------------------------------------------------

class CorreccionEnglish(BaseModel):
    transcription_check: str
    explanation: str
    grammar_topic: str
    natural_response: str


JSON_SCHEMA = CorreccionEnglish.model_json_schema()


# ---------------------------------------------------------------------------
# Prompt del sistema
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Role: Expert English Language Coach.
Philosophy: Do not let errors slide. Be direct and clear about mistakes, but remain encouraging. The user should notice the gap between what they said and native English -- but keep it brief and focused, not a full conversation.

Instructions:
1. If there is an error, "explanation" must start directly with the correction (never "Maybe you could...").
2. "natural_response" is what gets read aloud by a TTS engine. It must briefly acknowledge the correct version, then stop. No follow-up questions, no extended chat.
3. If the input is already correct, celebrate briefly in "natural_response" and set grammar_topic to "None".
4. Keep "natural_response" under 20 words. It is a quick spoken confirmation, not a conversation turn.

JSON Fields:
- "transcription_check": the corrected version of what the user said.
- "explanation": starts with "It should be '[correction]'." followed by one short sentence explaining why.
- "grammar_topic": one of [Subject-Verb Agreement, Prepositions, Verb Tenses, Word Choice, Articles, None].
- "natural_response": short spoken acknowledgment (under 20 words), no questions.

Examples:

Input: "She don't like pizza"
Output: {"transcription_check": "She doesn't like pizza", "explanation": "It should be 'She doesn't like pizza.' Third-person singular subjects need 'doesn't', not 'don't'.", "grammar_topic": "Subject-Verb Agreement", "natural_response": "Got it -- she doesn't like pizza."}

Input: "I went to the store yesterday and bought some milk"
Output: {"transcription_check": "I went to the store yesterday and bought some milk", "explanation": "Nice, that's correct.", "grammar_topic": "None", "natural_response": "Great, that was correct!"}

Strict rule: respond with ONLY the JSON object, nothing else, no markdown, no code fences.
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
            max_tokens=250,
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
        transcription_check=texto_transcrito,
        explanation="No se pudo analizar la frase en este momento.",
        grammar_topic="None",
        natural_response="Sorry, I couldn't process that. Could you try again?",
    )


def obtener_respuesta_tts(texto_transcrito: str) -> str:
    """
    Atajo para el pipeline de audio: toma la transcripcion y regresa solo el
    texto corto que TTS debe pronunciar.
    """
    resultado = analizar_texto(texto_transcrito)
    return resultado.natural_response.strip()
