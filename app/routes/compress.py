import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import verify_api_key
from app.models.job import (
    CompressRequest,
    CompressResponse,
    Job,
    JobStatus,
    JobStatusResponse,
)
from app.services import compression, queue

router = APIRouter()


@router.post(
    "/api/v1/compress",
    response_model=CompressResponse,
    status_code=202,
)
async def submit_compress_job(
    req: CompressRequest,
    _: str = Depends(verify_api_key),
):
    job = Job(
        job_id=str(uuid.uuid4()),
        source_url=req.source_url,
        webhook_url=req.webhook_url,
        manus_upload_url=req.manus_upload_url,
        options=req.options,
        ragic_config=req.ragic_config,
        metadata=req.metadata,
    )
    queue.create_job(job)
    asyncio.create_task(compression.process_job(job))
    return CompressResponse(job_id=job.job_id, status=job.status)


@router.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    _: str = Depends(verify_api_key),
):
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        result=job.result,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )
