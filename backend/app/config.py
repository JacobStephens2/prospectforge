from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_log_level: str = "info"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "prospectforge"
    postgres_user: str = "prospectforge"
    postgres_password: str = "changeme-local-only"

    redis_host: str = "redis"
    redis_port: int = 6379

    anthropic_api_key: str = ""
    anthropic_model_default: str = "claude-sonnet-4-5"
    pf_max_cost_cents_default: int = 2000
    pf_firecrawl_enabled: bool = False

    sentry_dsn: str = ""

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
