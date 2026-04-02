from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class JobStatus(str, Enum):
    queued = "queued"
    downloading = "downloading"
    assembling = "assembling"
    compressing = "compressing"
    uploading = "uploading"
    completed = "completed"
    failed = "failed"


class CompressOptions(BaseModel):
    quality: str = "medium"
    max_width: Optional[int] = None
    format: str = "mp4"


class RagicConfig(BaseModel):
    api_url: str
    api_key: str
    form_path: str
    record_id: str
    field_id: str


class ForgeConfig(BaseModel):
    """Forge S3 儲存設定 — 由呼叫方 (Manus) 傳入"""
    api_url: str
    api_key: str
    upload_path: str


class CompressRequest(BaseModel):
    source_url: Optional[str] = None
    source_urls: Optional[list[str]] = None
    webhook_url: Optional[str] = None
    options: CompressOptions = CompressOptions()
    ragic_config: Optional[RagicConfig] = None
    forge_config: Optional[ForgeConfig] = None
    skip_compress: Optional[bool] = None
    metadata: Optional[dict] = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_url and self.source_urls:
            raise ValueError("不可同時提供 source_url 和 source_urls，請擇一使用")
        if not self.source_url and not self.source_urls:
            raise ValueError("必須提供 source_url 或 source_urls 其中一個")
        if self.source_urls is not None and len(self.source_urls) == 0:
            raise ValueError("source_urls 不可為空陣列")
        return self


class CompressResult(BaseModel):
    download_url: str
    ragic_url: Optional[str] = None
    ragic_error: Optional[str] = None
    forge_url: Optional[str] = None
    forge_error: Optional[str] = None
    original_size_mb: float
    compressed_size_mb: float
    compression_ratio: float
    duration_seconds: Optional[float] = None
    resolution: Optional[str] = None


class Job(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.queued
    source_url: Optional[str] = None
    source_urls: Optional[list[str]] = None
    webhook_url: Optional[str] = None
    options: CompressOptions = CompressOptions()
    ragic_config: Optional[RagicConfig] = None
    metadata: Optional[dict] = None
    result: Optional[CompressResult] = None
    error: Optional[str] = None
    output_path: Optional[str] = None
    output_expires_at: Optional[datetime] = None
    original_url: Optional[str] = None
    original_path: Optional[str] = None
    original_expires_at: Optional[datetime] = None
    forge_config: Optional[ForgeConfig] = None
    skip_compress: Optional[bool] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


class CompressResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    result: Optional[CompressResult] = None
    error: Optional[str] = None
    original_url: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
