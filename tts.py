import logging
import os
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

from config import TTS_ONNX_PROVIDER
from manejo_rutas import ModeloRemoto, configurar_logging, configurar_rutas_cuda_windows, gestor_rutas

configurar_rutas_cuda_windows()
configurar_logging()
logger = logging.getLogger(__name__)
os.environ.setdefault("ONNX_PROVIDER", TTS_ONNX_PROVIDER)

MODEL_PATH = gestor_rutas.ruta_modelo("kokoro-v1.0.onnx")
VOICES_PATH = gestor_rutas.ruta_modelo("voices-v1.0.bin")
KOKORO_MODEL = ModeloRemoto(
    nombre="kokoro-onnx",
    ruta_local=MODEL_PATH,
    url_descarga="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
)
KOKORO_VOICES = ModeloRemoto(
    nombre="kokoro-voices",
    ruta_local=VOICES_PATH,
    url_descarga="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
)

# Instancia global para no recargar el modelo cada vez que hable
_kokoro_instance = None

def get_kokoro():
    global _kokoro_instance
    if _kokoro_instance is None:
        gestor_rutas.asegurar_modelo(KOKORO_MODEL)
        gestor_rutas.asegurar_modelo(KOKORO_VOICES)
        _kokoro_instance = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        logger.info("Modelo Kokoro listo con provider ONNX: %s", os.getenv("ONNX_PROVIDER"))
    return _kokoro_instance


def generar_wav(
    text: str,
    output_path: str,
    voice: str = "af_sarah",
    speed: float = 1.0,
    lang: str = "en-us",
) -> str:
    """
    Genera un archivo WAV en disco a partir del texto y devuelve su ruta.
    """
    texto_limpio = text.strip()
    if not texto_limpio:
        raise ValueError("No hay texto para convertir a audio.")

    kokoro = get_kokoro()
    samples, sample_rate = kokoro.create(
        texto_limpio,
        voice=voice,
        speed=speed,
        lang=lang,
    )

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_file), samples, sample_rate, format="WAV")
    return str(output_file.resolve())

def speak(text: str, voice: str = "af_sarah", speed: float = 1.0, lang: str = "en-us", volume=1.0):
    """
    Genera y reproduce el audio a partir del texto directamente en memoria 
    usando sounddevice (sin guardarlo en el disco). 
    Permite volumen en tiempo real pasándole una función (lambda) en 'volume'.
    """
    try:
        import threading
        import numpy as np
        import sounddevice as sd
        
        kokoro = get_kokoro()
        
        print(f"Generando audio para: '{text}'...")
        # Generar el audio
        samples, sample_rate = kokoro.create(
            text, 
            voice=voice, 
            speed=speed, 
            lang=lang
        )
        
        # Agregar 0.4s de silencio al final para evitar el corte abrupto de audio
        silence = np.zeros(int(sample_rate * 0.4), dtype=samples.dtype)
        samples = np.concatenate([samples, silence])
        
        print("Reproduciendo audio con soporte en tiempo real...")
        
        current_idx = 0
        event = threading.Event()
        
        def callback(outdata, frames, time_info, status):
            nonlocal current_idx
            if status:
                print(status)
                
            chunk = samples[current_idx:current_idx + frames]
            
            # Obtener el volumen actual en tiempo real
            v = volume() if callable(volume) else volume
            
            if len(chunk) < frames:
                outdata[:len(chunk), 0] = chunk * v
                outdata[len(chunk):, 0] = 0
                event.set()
                raise sd.CallbackStop()
            else:
                outdata[:, 0] = chunk * v
                
            current_idx += frames
            
        with sd.OutputStream(samplerate=sample_rate, channels=1, callback=callback):
            # Calcular duración total del audio en segundos
            duration_sec = len(samples) / sample_rate
            # Esperar a que termine, o abortar si tarda más de (duración + 2) segundos
            # Esto evita que si SoundDevice se cuelga en Windows, la interfaz no se quede atascada.
            event.wait(timeout=duration_sec + 2.0)
        
    except Exception as e:
        print(f"Error al generar/reproducir audio: {e}")

if __name__ == "__main__":
    # Prueba rápida si ejecutas python TTS.py directamente
    texto_prueba = "Hello, this is a direct playback test using sound device! No files are saved to your disk."
    speak(texto_prueba)
