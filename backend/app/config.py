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

    # CRM Bridge (read-only sync target on the live CRM MySQL).
    crm_db_host: str = "crm-db.acme-travel.example"
    crm_db_port: int = 3306
    crm_db_name: str = "crm_db"
    crm_db_user: str = "prospectforge_readonly"
    crm_db_password: str = ""

    anthropic_api_key: str = ""
    anthropic_model_default: str = "claude-sonnet-4-5"
    pf_max_cost_cents_default: int = 2000
    pf_firecrawl_enabled: bool = False

    # Firecrawl self-hosted endpoint. Reachable from the backend container
    # over the docker network as `http://firecrawl-api:3002`. No auth header
    # required when USE_DB_AUTHENTICATION=false (see docker-compose.yml).
    firecrawl_base_url: str = "http://firecrawl-api:3002"
    firecrawl_request_timeout_secs: float = 120.0

    # Comma-separated CIDRs / IPs to add to the SSRF denylist on top of the
    # always-blocked ranges (private, loopback, link-local, metadata).
    # Defaulted to the Acme internal IPs we know we never want to scrape.
    pf_ssrf_internal_ip_denylist: str = (
        "203.0.113.10,203.0.113.11,143.244.168.75,192.241.157.189"
    )

    sentry_dsn: str = ""

    # SERP Tier-0 for per-contact enrichment. When a provider key is set,
    # enrichment_run uses `site:domain "Name"` to locate the single person
    # page before falling back to a broad org-domain crawl. Empty = disabled
    # (returns no candidate → crawl runs unchanged). Brave preferred over
    # SerpAPI when both set (cheaper, generous free tier).
    serp_provider: str = ""           # "brave" | "serpapi" | ""
    serp_brave_api_key: str = ""
    serp_serpapi_api_key: str = ""
    serp_request_timeout_secs: float = 8.0
    serp_max_results_inspected: int = 5

    # Auth / email
    app_public_url: str = "https://prospect.acme-travel.example"
    smtp_host: str = "smtp.mandrillapp.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "info@acme-travel.example"
    mail_dev_mode: bool = False
    auth_session_days: int = 30
    auth_magic_link_minutes: int = 15

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
