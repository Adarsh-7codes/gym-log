from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./gym.db"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Set to true in production (Railway/Render serve https) so the auth
    # cookie is only ever sent over an encrypted connection.
    cookie_secure: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        # Render (and Heroku) expose Postgres as postgres://... but SQLAlchemy 2.x
        # only accepts the postgresql:// scheme. Normalize so the same env var
        # works verbatim in the Render dashboard.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        return v


settings = Settings()
