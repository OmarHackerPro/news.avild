import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "news.avild.com")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # PostgreSQL — postgresql+asyncpg://user:password@host:port/dbname
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # CORS — comma-separated allowed origins
    CORS_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv(
            "CORS_ORIGINS",
            "http://localhost,http://localhost:8000,http://127.0.0.1,http://127.0.0.1:8000",
        ).split(",")
        if o.strip()
    ]

    # Auth / JWT
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 days

    # SMTP (password-reset emails)
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "noreply@news.avild.com")

    # Base URL used in password-reset email links
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000")

    # Admin secret — required for /api/admin/* endpoints
    ADMIN_SECRET: str = os.getenv("ADMIN_SECRET", "")


settings = Settings()
