import logging
import logging.handlers
import os
import sys
from datetime import datetime


def setup_logging(log_level: str = "INFO", log_dir: str = "./logs"):
    """
    Настраивает логирование для всего проекта:
    - Логи пишутся в файлы с ротацией
    - Отдельные логгеры для разных компонентов
    - Логи в stdout для Docker
    """
    os.makedirs(log_dir, exist_ok=True)

    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Удаляем все существующие обработчики, чтобы избежать дублей
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Обработчик для stdout (консоль)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(log_format, date_format)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # Обработчик для файлов с ротацией (общий)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "hermes_bridge.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(log_format, date_format)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Специализированные логгеры для компонентов
    component_loggers = [
        "orchestrator",
        "ws_server",
        "telegram_bot.handlers",
        "summarizer",
        "database",
        "main",
        "extension_manager",
        "hermes_agent"  # новый логгер для Hermes
    ]
    for name in component_loggers:
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)

        # Для каждого компонента свой файл
        component_file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, f"{name.replace('.', '_')}.log"),
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3
        )
        component_file_handler.setLevel(logging.DEBUG)
        component_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s", date_format
        )
        component_file_handler.setFormatter(component_formatter)
        logger.addHandler(component_file_handler)

    # Отключаем излишний шум от библиотек
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    root_logger.info("=== Logging initialized ===")
    root_logger.info(f"Log directory: {os.path.abspath(log_dir)}")
    root_logger.info(f"Log level: {log_level}")
