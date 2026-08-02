# TRIAS

TRIAS es un tutor local de inglés en Telegram. Acepta una frase escrita o un audio corto, detecta si la entrada está en inglés, entrega una corrección breve en español y envía una pronunciación modelo en inglés.

El proyecto se ejecuta desde `C:\Users\FabiisLK\Desktop\dev\SI\TRIAS`; esa es la copia oficial, no la carpeta sincronizada de Drive.

## Qué hace

- Audio: Telegram → Whisper (CPU) → Llama (GPU) → Kokoro (CPU) → voz de Telegram.
- Texto: Telegram → Llama (GPU) → Kokoro (CPU) → voz de Telegram.
- Rechaza audio o texto que no pueda confirmar como inglés, sin pedir corrección al modelo ni generar voz.
- Guarda únicamente progreso agregado por usuario: último tema y conteos por tema. No guarda frases, transcripciones ni audios.
- Protege el modelo frente a instrucciones insertadas en la frase: solo analiza gramática, vocabulario, naturalidad y uso.

## Preparación

1. Crea `.env` a partir de `.env.example`:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Configura como mínimo `TELEGRAM_BOT_TOKEN`, `INVITE_CODE` y los IDs de administración. Nunca compartas el archivo `.env`.
3. Comprueba que los modelos estén en `models/` y que la ruta de `LLAMA_MODEL_PATH` sea correcta.
4. Usa el entorno virtual local `venv`. Llama usa la RTX con `LLAMA_GPU_LAYERS=-1`; Whisper y Kokoro se configuran en CPU con `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE_TYPE=int8` y `TTS_ONNX_PROVIDER=CPUExecutionProvider`.

La detección de idioma de Whisper se controla con `MIN_LANGUAGE_CONFIDENCE=0.70`. Si el audio tiene una confianza menor, TRIAS pide repetirlo para evitar inventar una corrección.

## Arrancar y detener

Desde PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
& "C:\Users\FabiisLK\Desktop\dev\SI\TRIAS\run_trias.ps1"
```

El script valida que estás en la copia local, usa `venv\Scripts\python.exe` y ejecuta `main.py`. Para detener el servicio, presiona `Ctrl+C` en esa terminal.

## Comandos del bot

- `/start <codigo>` o `/access <codigo>`: autoriza al usuario con el código de invitación.
- `/help`: muestra ayuda.
- `/practice`: propone y pronuncia una frase del último tema que necesitó práctica; si no hay historial, propone una frase general.
- `/progress`: muestra el último tema y los tres temas que más han necesitado corrección.
- Administración: `/allow <user_id>`, `/revoke <user_id>`, `/users` (solo IDs administradores).

Los comandos no se analizan como frases de inglés. Texto y audio comparten una sola cola para evitar inferencias simultáneas.

## Verificación

Ejecuta las pruebas desde la carpeta del proyecto:

```powershell
& .\venv\Scripts\python.exe -m pytest -q
```

Para la demostración manual de clase, sigue [DEMO_TESTS.md](DEMO_TESTS.md).
