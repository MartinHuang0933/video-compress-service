from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    downloading = "downloading"
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


class CompressRequest(BaseModel):
    source_url: str
    webhook_url: Optional[str] = None
    manus_upload_url: Optional[str] = None
    options: CompressOptions = CompressOptions()
    ragic_config: Optional[RagicConfig] = None
    metadata: Optional[dict] = None


class CompressResult(BaseModel):
    compressed_s3_url: Optional[str] = None
    ragic_url: Optional[str] = None
    ragic_error: Optional[str] = None
    original_size_mb: float
    compressed_size_mb: float
    compression_ratio: float
    duration_seconds: Optional[float] = None
    resolution: Optional[str] = None


class Job(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.queued
    source_url: str
    webhook_url: Optional[str] = None
    manus_upload_url: Optional[str] = None
    options: CompressOptions = CompressOptions()
    ragic_config: Optional[RagicConfig] = None
    metadata: Optional[dict] = None
    result: Optional[CompressResult] = None
    error: Optional[str] = None
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
    created_at: datetime
    completed_at: Optional[datetime] = None
