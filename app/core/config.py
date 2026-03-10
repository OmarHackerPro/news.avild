import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "news.avild.com")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    # PostgreSQL — local or cloud (Supabase, Neon, RDS, etc.)
    # Format: postgresql+asyncpg://user:password@host:port/dbname
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Auth / JWT
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 days

    # SMTP (password reset emails)
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "noreply@kiber.info")

    # Base URL for password-reset email links
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000")

    # OpenSearch
    OPENSEARCH_URL: str = os.getenv("OPENSEARCH_URL", "")
    OPENSEARCH_USER: str = os.getenv("OPENSEARCH_USER", "")
    OPENSEARCH_PASSWORD: str = os.getenv("OPENSEARCH_PASSWORD", "")


settings = Settings()
