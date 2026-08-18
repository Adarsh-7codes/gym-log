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

    # Trainer-first product: members are created by the trainer, not by
    # strangers who find the URL. Self-registration stays available only to
    # bootstrap the very first account (the trainer) on an empty database.
    allow_open_registration: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_sqlite(self) -> bool:
        """True when running on the local SQLite file, false on Render/Postgres."""
        return self.database_url.startswith("sqlite")

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
