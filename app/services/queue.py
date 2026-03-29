import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.models.job import Job, JobStatus

_jobs: dict[str, Job] = {}

_semaphore: asyncio.Semaphore | None = None


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
    return _semaphore


def create_job(job: Job) -> Job:
    _jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def update_job_status(
    job_id: str,
    status: JobStatus,
    error: Optional[str] = None,
    result=None,
) -> Optional[Job]:
    job = _jobs.get(job_id)
    if job is None:
        return None
    job.status = status
    if error is not None:
        job.error = error
    if result is not None:
        job.result = result
    if status in (JobStatus.completed, JobStatus.failed):
        job.completed_at = datetime.now(timezone.utc)
    return job
