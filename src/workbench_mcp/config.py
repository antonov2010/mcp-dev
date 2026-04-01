"""Configuration management and environment variable loading for the workbench system."""
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


load_dotenv(override=False)


class Settings(BaseSettings):
    """Centralized configuration for database connections and API endpoints."""

    db_host: str  # PostgreSQL server hostname
    db_port: int = 5432  # Port number (default PostgreSQL port)
    db_name: str  # Database name to connect to
    db_user: str  # Username for authentication
    db_password: SecretStr  # Password (stored securely)
    db_sslmode: str = "prefer"  # SSL connection mode: prefer, require, disable, allow, or disable
    db_application_name: str = "workbench-mcp"  # Application name reported to PostgreSQL
    db_query_timeout_seconds: int = 30  # Query execution timeout in seconds
    db_max_rows: int = 100  # Maximum rows per result set
    db_max_result_sets: int = 5  # Maximum result sets per batch
    db_object_preview_chars: int = 4000  # Maximum characters for object definition preview

    # HTTP request tuning
    api_verify_ssl: bool = True  # Verify SSL certificates for HTTP requests
    api_timeout_seconds: float = 30.0  # HTTP request timeout in seconds
    api_max_response_bytes: int = 2_097_152  # Maximum response size (2 MB default)
    api_bearer_token: SecretStr | None = None  # Optional Bearer token for HTTP requests

    @field_validator(
        "db_application_name",
        mode="before",
    )
    @classmethod
    def empty_str_api_optional(cls, value: object) -> object:
        """Treat empty strings as None for optional configuration fields."""
        if value == "":
            return None
        return value

    @field_validator("api_bearer_token", mode="before")
    @classmethod
    def empty_api_bearer_token_to_none(cls, value: object) -> object:
        """Treat empty API bearer token values as None."""
        if value == "":
            return None
        return value

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    def connection_kwargs(self) -> dict[str, Any]:
        """Generate psycopg connection parameters from configuration."""
        return {
            "host": self.db_host,
            "port": self.db_port,
            "dbname": self.db_name,
            "user": self.db_user,
            "password": self.db_password.get_secret_value(),
            "sslmode": self.db_sslmode,
            "connect_timeout": max(1, self.db_query_timeout_seconds),
            "application_name": self.db_application_name,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load configuration from environment variables (cached singleton)."""
    return Settings()
