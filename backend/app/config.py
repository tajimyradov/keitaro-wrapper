from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    keitaro_url: str = "https://tlgk.host"
    keitaro_api_key: str = ""
    campaign_prefix: str = "test_"
    database_url: str = "sqlite:///./app.db"


settings = Settings()


def full_name(name: str) -> str:
    """Добавляет обязательный префикс test_фамилия_, если его ещё нет."""
    name = name.strip()
    return name if name.startswith(settings.campaign_prefix) else settings.campaign_prefix + name
