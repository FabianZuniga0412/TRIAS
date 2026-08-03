# Apéndice

El presente apéndice reúne la documentación técnica complementaria del proyecto TRIAS, con énfasis en los módulos que coordinan las tres inteligencias artificiales, controlan la seguridad de las entradas y mantienen un historial pedagógico mínimo. Su propósito es facilitar la reproducibilidad del sistema y explicar las decisiones técnicas principales.

## Código fuente de los módulos críticos

### Módulo orquestador de Telegram: `bot.py`

El módulo `bot.py` concentra la interacción con Telegram y actúa como orquestador de la arquitectura agéntica controlada. Valida el acceso del usuario, recibe texto o audio, controla una cola única de trabajos y decide cuándo invocar a Whisper, Llama y Kokoro.

La clase `LearningJob` representa una solicitud de aprendizaje. Puede contener una entrada de tipo `audio`, `text` o `practice`.

```python
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
```

La función `_enqueue()` evita que un mismo usuario mantenga más de una solicitud pendiente y verifica que la cola no alcance el límite configurado.

```python
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
```

La cola es procesada por un único worker. Esta decisión evita que varias inferencias locales compitan al mismo tiempo por CPU, GPU y memoria.

```python
async def _worker(self) -> None:
    while True:
        job = await self.queue.get()
        try:
            await self._process_job(job)
        finally:
            self.queue.task_done()
```

### Procesamiento de audio y validación de idioma

Cuando el trabajo es de tipo `audio`, el bot descarga temporalmente el archivo, lo convierte a WAV y delega la transcripción a Whisper. La respuesta de Whisper incluye texto, idioma y confianza.

```python
if job.kind == "audio":
    from transcriptor import transcribir_audio

    input_path = new_input_path(job.filename)
    await download_telegram_file(self.application.bot, job.file_id, input_path)
    wav_path = await run_blocking(convert_to_wav, input_path)
    transcription = await run_blocking(transcribir_audio, str(wav_path))

    if not transcription.text or transcription.language_probability < MIN_LANGUAGE_CONFIDENCE:
        await self.application.bot.send_message(job.chat_id, EMPTY_TRANSCRIPTION)
        return
    if transcription.language != "en":
        await self.application.bot.send_message(job.chat_id, NON_ENGLISH)
        return
    text = transcription.text
```

El umbral `MIN_LANGUAGE_CONFIDENCE` tiene un valor inicial de 0.70. Si Whisper no produce texto, tiene confianza baja o detecta un idioma diferente del inglés, el proceso termina antes de ejecutar Llama y Kokoro. Esto reduce la posibilidad de generar una corrección a partir de ruido, silencio o contenido no relacionado con la práctica de inglés.

### Integración del agente tutor y la voz modelo

Después de confirmar la entrada, el orquestador solicita el análisis a Llama. Solo si la salida confirma que el idioma es inglés se registra el tema de aprendizaje, se muestra el feedback y se genera la voz modelo.

```python
analysis = await run_blocking(self._run_analysis, text)
if analysis["input_language"] != "en":
    await self.application.bot.send_message(job.chat_id, NON_ENGLISH)
    return

if analysis["assessment"] in {"needs_correction", "correct_but_unnatural"}:
    self.history_store.record(job.user_id, analysis["focus"])

await self.application.bot.send_message(
    job.chat_id,
    self._format_feedback(text, analysis, job.kind),
)
generated_wav = new_output_path(".wav")
generated_wav = Path(await run_blocking(
    self._run_tts,
    analysis["corrected_text"],
    str(generated_wav),
))
```

El archivo WAV se transforma a OGG Opus antes de enviarse como nota de voz. En el bloque `finally` se eliminan los archivos de entrada y salida temporales, incluso cuando ocurre una excepción.

```python
finally:
    remove_files([input_path, wav_path, generated_wav, ogg_path])
    async with self.pending_lock:
        self.pending_user_ids.discard(job.user_id)
```

### Módulo de transcripción: `transcriptor.py`

El módulo `transcriptor.py` carga Faster Whisper una sola vez y define una estructura de salida para no perder la información de idioma durante la transcripción.

```python
@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    language_probability: float


def transcribir_audio(wav_path: str) -> TranscriptionResult:
    segments, info = model.transcribe(
        wav_path,
        beam_size=5,
        vad_filter=True,
    )
    texto = " ".join(segment.text.strip() for segment in segments)
    return TranscriptionResult(
        texto.strip(),
        info.language,
        float(info.language_probability),
    )
```

Whisper no recibe el idioma inglés como una instrucción obligatoria. De esta manera puede detectar entradas en español u otros idiomas y el bot puede detener el flujo antes de pedir una corrección a Llama.

### Contrato pedagógico y defensa de salida: `tutor_contract.py`

El archivo `tutor_contract.py` define el contrato que debe cumplir Llama. La salida no puede contener campos arbitrarios ni temas ajenos a la enseñanza de inglés.

```python
class CorreccionEnglish(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input_language: Literal["en", "other", "uncertain"]
    assessment: Assessment
    corrected_text: str = Field(min_length=1, max_length=280)
    natural_alternative: str = Field(default="", max_length=280)
    explanation_es: str = Field(min_length=1, max_length=360)
    focus: Focus
```

Los temas válidos están limitados a concordancia entre sujeto y verbo, tiempo verbal, artículos, preposiciones, elección de palabras, naturalidad y estructura de oración. El contrato también rechaza bloques de código, texto en varias líneas, frases extensas y patrones que intenten adoptar el rol de un asistente técnico.

La entrada del estudiante se delimita como contenido no confiable.

```python
def learner_message(text: str) -> str:
    return "<learner_sentence>\n" + text.strip() + "\n</learner_sentence>"
```

Esta separación permite indicar al modelo que una orden incluida en la frase no debe ejecutarse. Por ejemplo, una solicitud sobre Arduino puede ser analizada como oración en inglés, pero no debe convertirse en una explicación técnica ni en código.

### Módulo de análisis con Llama: `llm.py`

El archivo `llm.py` carga el modelo Llama local con `llama-cpp-python` y solicita una respuesta en formato JSON compatible con `CorreccionEnglish`. La salida se valida antes de regresar al bot.

```python
data = json.loads(contenido)
return CorreccionEnglish(**data)
```

Si el modelo produce JSON inválido o una respuesta que no cumple el contrato, el sistema realiza reintentos. Si continúa fallando, devuelve una respuesta segura que solicita al usuario enviar una frase corta en inglés.

```python
return CorreccionEnglish(
    input_language="uncertain",
    assessment="unable_to_analyze",
    corrected_text="Please send a short English sentence.",
    natural_alternative="",
    explanation_es="No pude analizar esa entrada de forma segura. Envía una frase corta para practicar inglés.",
    focus="Sentence structure",
)
```

### Módulo de síntesis de voz: `tts.py`

El módulo `tts.py` usa Kokoro ONNX para convertir la frase modelo en audio. La instancia del modelo se conserva en memoria después de la primera carga para evitar recargarla en cada solicitud.

```python
def generar_wav(
    text: str,
    output_path: str,
    voice: str = "af_sarah",
    speed: float = 1.0,
    lang: str = "en-us",
) -> str:
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
```

Kokoro recibe solamente `corrected_text`. La explicación en español no se sintetiza, lo que mantiene la respuesta oral corta y centrada en la pronunciación.

### Módulo de autorización: `authorization.py`

El control de acceso se mantiene separado de la lógica de aprendizaje. La lista efectiva combina los usuarios configurados inicialmente, los usuarios autorizados mediante invitación y los administradores. Los administradores conservan acceso aunque el archivo local sea modificado.

```python
def effective_users(self) -> set[int]:
    return ((set(self.base_users) | self.added_users) - self.revoked_users) | set(self.admins)


def allow(self, user_id: int) -> bool:
    was_authorized = self.is_authorized(user_id)
    self.revoked_users.discard(user_id)
    self.added_users.add(user_id)
    self._save()
    return not was_authorized
```

El código de invitación se compara con `hmac.compare_digest`, evitando una comparación simple de cadenas. Los comandos administrativos disponibles son `/allow`, `/revoke` y `/users`.

### Módulo de historial pedagógico: `learning_history.py`

El historial pedagógico no conserva contenido lingüístico del alumno. Guarda únicamente el último foco y contadores agregados por tema. También utiliza un catálogo local y determinista para el comando `/practice`.

```python
def record(self, user_id: int, focus: str) -> None:
    if focus not in PRACTICE_CATALOG:
        raise ValueError(f"Tema de práctica no permitido: {focus}")
    entry = self.users.setdefault(
        str(user_id),
        {"last_focus": None, "focus_counts": {}},
    )
    entry["last_focus"] = focus
    counts = entry["focus_counts"]
    counts[focus] = int(counts.get(focus, 0)) + 1
    self._save()
```

El historial solo se actualiza cuando Llama identifica `needs_correction` o `correct_but_unnatural`. Una frase correcta y natural no incrementa ningún contador.

## Configuración y despliegue

### Archivo de dependencias: `requirements.txt`

El proyecto define las dependencias necesarias para reproducir el entorno de ejecución.

| Dependencia | Versión | Propósito técnico dentro de TRIAS |
|---|---:|---|
| faster-whisper | 1.2.1 | Transcripción de voz y detección de idioma. |
| llama-cpp-python | 0.3.34 | Ejecución local del modelo Llama en formato GGUF. |
| kokoro-onnx | 0.5.0 | Síntesis de voz local. |
| onnxruntime | 1.24.4 | Ejecución del modelo ONNX de Kokoro. |
| python-telegram-bot | 21.10 | Comunicación con la API de Telegram. |
| pydantic | 2 o superior | Validación del contrato JSON de Llama. |
| lingua-language-detector | 2.2.0 | Detección local previa de idioma en mensajes de texto. |
| ffmpeg-python | 0.2.0 | Integración de conversión de audio con FFmpeg. |
| python-dotenv | 1.1.0 | Carga de configuración local desde `.env`. |
| pytest | 8 o superior | Pruebas automatizadas del sistema. |

Adicionalmente se utilizan las bibliotecas `nvidia-cublas-cu12` y `nvidia-cuda-runtime-cu12` para que la instalación de Llama pueda usar CUDA en Windows.

### Variables de entorno requeridas

Las variables se almacenan en `.env`. El archivo real no debe compartirse, subirse a Git ni incluirse en capturas de pantalla.

| Variable | Uso en el sistema |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token privado entregado por BotFather para operar el bot. |
| `AUTHORIZED_USER_IDS` | IDs iniciales de usuarios con acceso. |
| `ADMIN_USER_IDS` | IDs con permisos administrativos. |
| `ACCESS_MODE` | Define si el acceso funciona con invitación o modo cerrado. |
| `INVITE_CODE` | Código requerido para autorizar usuarios nuevos. |
| `MAX_AUDIO_SECONDS` | Duración máxima permitida para un audio. |
| `MAX_QUEUE_SIZE` | Número máximo de trabajos en espera. |
| `MIN_LANGUAGE_CONFIDENCE` | Confianza mínima de Whisper para aceptar el audio. |
| `MIN_TEXT_LANGUAGE_CONFIDENCE` | Confianza mínima para bloquear texto detectado como otro idioma. |
| `WHISPER_MODEL` | Tamaño del modelo Whisper utilizado. |
| `WHISPER_DEVICE` | Dispositivo para Whisper, configurado en CPU. |
| `WHISPER_COMPUTE_TYPE` | Tipo de cálculo de Whisper, configurado como int8. |
| `LLAMA_MODEL_PATH` | Ruta local del archivo GGUF de Llama. |
| `LLAMA_GPU_LAYERS` | Capas de Llama enviadas a la GPU. |
| `LLAMA_CTX` | Tamaño de contexto configurado para Llama. |
| `TTS_VOICE` | Voz seleccionada para Kokoro. |
| `TTS_SPEED` | Velocidad de pronunciación. |
| `TTS_LANG` | Variante de inglés para síntesis de voz. |
| `TTS_ONNX_PROVIDER` | Proveedor ONNX, configurado en CPU. |

### Inicio manual del servicio

El script `run_trias.ps1` valida que el proyecto se encuentre en la copia local, comprueba la existencia del entorno virtual y ejecuta el punto de entrada `main.py`.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
& "C:\Users\FabiisLK\Desktop\dev\SI\TRIAS\run_trias.ps1"
```

El servicio se detiene con `Ctrl+C` en la terminal donde se ejecutó el script.

## Estructura de datos

TRIAS mantiene dos archivos JSON separados. Uno controla acceso y otro conserva progreso pedagógico. Esta separación evita mezclar información de autorización con información de aprendizaje.

### Archivo de autorizaciones

Ruta local:

```text
data/authorized_users.json
```

Fragmento JSON de ejemplo. Los identificadores son ficticios.

```json
{
  "added_user_ids": [123456789],
  "revoked_user_ids": []
}
```

### Archivo de historial pedagógico

Ruta local:

```text
data/learning_history.json
```

Fragmento JSON de ejemplo. No contiene frases, transcripciones ni audio.

```json
{
  "users": {
    "123456789": {
      "last_focus": "Subject-verb agreement",
      "focus_counts": {
        "Subject-verb agreement": 2,
        "Articles": 1
      }
    }
  }
}
```

Los únicos focos aceptados por el historial son `Subject-verb agreement`, `Verb tense`, `Articles`, `Prepositions`, `Word choice`, `Natural phrasing` y `Sentence structure`.

## Validación y pruebas

Las pruebas se encuentran en la carpeta `tests/`. Se utilizan objetos simulados para comprobar la lógica del bot sin depender de Telegram, de los modelos pesados ni de una conexión durante la prueba.

| Archivo de prueba | Aspectos validados |
|---|---|
| `test_bot.py` | Acceso, límite de texto, cola compartida, texto, audio, idioma, práctica, progreso y limpieza de temporales. |
| `test_tutor_contract.py` | Campos permitidos, temas cerrados, límites de longitud, bloques de código, instrucciones ocultas y clasificación de idioma. |
| `test_learning_history.py` | Persistencia, restauración, conteos agregados y práctica general o personalizada. |

La ejecución se realiza con:

```powershell
& .\venv\Scripts\python.exe -m pytest -q
```

La versión documentada contiene 29 pruebas automatizadas exitosas. Para la demostración manual se incluyen escenarios de audio con error, texto poco natural, texto correcto, entrada no inglesa y prompt injection en `DEMO_TESTS.md`.

## Estructura general del proyecto

```text
TRIAS/
│   main.py
│   bot.py
│   config.py
│   authorization.py
│   learning_history.py
│   transcriptor.py
│   llm.py
│   tutor_contract.py
│   tts.py
│   audio_utils.py
│   run_trias.ps1
│   requirements.txt
│
├── data/
│   ├── authorized_users.json
│   └── learning_history.json
│
├── models/
│   ├── Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
│   ├── kokoro-v1.0.onnx
│   └── voices-v1.0.bin
│
├── outputs/
├── logs/
└── tests/
    ├── test_bot.py
    ├── test_learning_history.py
    └── test_tutor_contract.py
```

### Idea técnica principal

**TRIAS utiliza un orquestador que valida la entrada, delega voz a Whisper, tutoría a Llama y pronunciación a Kokoro, mientras mantiene acceso controlado, historial mínimo y validación de seguridad.**
