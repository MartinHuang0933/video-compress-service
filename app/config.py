from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 8080
    api_key: str = ""
    base_url: str = ""

    # Compression
    default_quality: str = "medium"
    max_file_size_mb: int = 1000
    max_concurrent_jobs: int = 2
    temp_dir: str = "/tmp/video-compress"
    file_retention_minutes: int = 60

    model_config = {"env_file": ".env"}


settings = Settings()
