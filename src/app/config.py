from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://localhost:5432/deal_screening"

    @model_validator(mode="after")
    def normalize_database_url(self):
        """Fly Postgres sets DATABASE_URL as postgres://... but SQLAlchemy needs postgresql+asyncpg://..."""
        url = self.database_url
        if url.startswith("postgres://"):
            self.database_url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            self.database_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self

    # OpenAI (for extraction service)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Storage
    upload_dir: str = "./uploads"
    max_file_size_bytes: int = 50 * 1024 * 1024  # 50MB

    # Auth
    jwt_secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 15

    # Rate limiting
    rate_limit_per_minute: int = 100

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
