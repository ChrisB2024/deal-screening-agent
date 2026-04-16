from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://localhost:5432/deal_screening"

    @model_validator(mode="after")
    def normalize_database_url(self):
        """Fly Postgres sets DATABASE_URL as postgres://... but SQLAlchemy needs postgresql+asyncpg://...
        Also strips sslmode param which asyncpg doesn't support as a query arg."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg doesn't accept sslmode — strip it; ssl is handled via connect_args
        if "sslmode=" in url:
            from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            params.pop("sslmode", None)
            cleaned_query = urlencode(params, doseq=True)
            url = urlunparse(parsed._replace(query=cleaned_query))
        self.database_url = url
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
