import os

class Settings:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://deepseek:secret@db/deepseek_bot"
    )
    EXTENSION_WS_HOST: str = "0.0.0.0"
    EXTENSION_WS_PORT: int = int(os.getenv("EXTENSION_WS_PORT", "8765"))
    MAX_CONTEXT_TOKENS: int = 90000
    SUMMARIZE_AFTER_MESSAGES: int = 20
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./uploads")
    VISION_ENABLED: bool = True
    USE_ML_CLASSIFIER: bool = True  # fallback на модель для неоднозначных случаев
    WS_AUTH_KEY: str = "supersecretkey"  # заменить в продакшене

settings = Settings()