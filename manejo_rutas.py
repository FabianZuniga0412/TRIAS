import logging
import os
import site
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModeloRemoto:
    nombre: str
    ruta_local: Path
    url_descarga: str | None = None


class GestorRutas:
    """
    Centraliza rutas del proyecto y la preparacion de recursos compartidos.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parent
        self.models_dir = self.base_dir / "models"
        self.logs_dir = self.base_dir / "logs"
        self.data_dir = self.base_dir / "data"
        self.outputs_dir = self.base_dir / "outputs"
        self.audio_entrada_dir = self.outputs_dir / "input_audio"
        self.audio_salida_dir = self.outputs_dir / "output_audio"

    def preparar_estructura(self) -> None:
        for ruta in (
            self.models_dir,
            self.logs_dir,
            self.data_dir,
            self.outputs_dir,
            self.audio_entrada_dir,
            self.audio_salida_dir,
        ):
            ruta.mkdir(parents=True, exist_ok=True)

    def archivo_log(self, nombre: str = "echosync.log") -> Path:
        self.preparar_estructura()
        return self.logs_dir / nombre

    def ruta_modelo(self, nombre_archivo: str) -> Path:
        self.preparar_estructura()
        return self.models_dir / nombre_archivo

    def ruta_audio_salida(self, nombre_archivo: str) -> Path:
        self.preparar_estructura()
        return self.audio_salida_dir / nombre_archivo

    def archivo_datos(self, nombre_archivo: str) -> Path:
        self.preparar_estructura()
        return self.data_dir / nombre_archivo

    def asegurar_modelo(self, modelo: ModeloRemoto) -> Path:
        self.preparar_estructura()
        if modelo.ruta_local.exists():
            return modelo.ruta_local

        if not modelo.url_descarga:
            raise FileNotFoundError(
                f"Falta el modelo '{modelo.nombre}' en {modelo.ruta_local} y no hay URL de descarga configurada."
            )

        logging.getLogger(__name__).info(
            "Descargando modelo '%s' en %s", modelo.nombre, modelo.ruta_local
        )
        urllib.request.urlretrieve(modelo.url_descarga, str(modelo.ruta_local))
        return modelo.ruta_local


gestor_rutas = GestorRutas()
gestor_rutas.preparar_estructura()


def configurar_logging(nombre_log: str = "echosync.log") -> Path:
    """
    Inicializa logging hacia consola y archivo si aun no existe configuracion.
    """
    log_path = gestor_rutas.archivo_log(nombre_log)
    root_logger = logging.getLogger()

    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            handlers=[
                logging.FileHandler(log_path, encoding="utf-8"),
                logging.StreamHandler(),
            ],
        )

    return log_path


def configurar_rutas_cuda_windows() -> list[Path]:
    """
    En Windows, agrega al proceso las carpetas con DLLs CUDA instaladas por pip.
    Esto permite que librerias como llama-cpp-python encuentren cublas/cudart.
    """
    agregadas: list[Path] = []

    if os.name != "nt":
        return agregadas

    candidatos_base = [Path(p) for p in site.getsitepackages() if Path(p).exists()]

    rutas_relativas = (
        Path("nvidia/cublas/bin"),
        Path("nvidia/cufft/bin"),
        Path("nvidia/curand/bin"),
        Path("nvidia/cusolver/bin"),
        Path("nvidia/cusparse/bin"),
        Path("nvidia/cuda_runtime/bin"),
        Path("nvidia/cudnn/bin"),
        Path("nvidia/cuda_nvrtc/bin"),
        Path("nvidia/nvjitlink/bin"),
        Path("llama_cpp/lib"),
    )

    for base in candidatos_base:
        for relativa in rutas_relativas:
            ruta = base / relativa
            if not ruta.exists():
                continue

            try:
                os.add_dll_directory(str(ruta))
            except (AttributeError, FileNotFoundError):
                pass

            os.environ["PATH"] = str(ruta) + os.pathsep + os.environ.get("PATH", "")
            agregadas.append(ruta)

    return agregadas
