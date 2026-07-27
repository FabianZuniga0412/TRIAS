"""Punto de entrada del bot de Telegram."""

from bot import build_application
from config import TELEGRAM_BOT_TOKEN, validate_bot_config
from manejo_rutas import configurar_logging


def main() -> None:
    configurar_logging()
    validate_bot_config()
    application = build_application(TELEGRAM_BOT_TOKEN)
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
