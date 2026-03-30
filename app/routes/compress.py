import asyncio
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

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


@router.get("/api/v1/jobs/{job_id}/download")
async def download_compressed_file(
    job_id: str,
    _: str = Depends(verify_api_key),
):
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.completed:
        raise HTTPException(status_code=400, detail="Job not completed")
    if not job.output_path or not os.path.exists(job.output_path):
        raise HTTPException(status_code=410, detail="File expired and has been cleaned up")

    file_size = os.path.getsize(job.output_path)

    def iter_file():
        with open(job.output_path, "rb") as f:
            while chunk := f.read(8 * 1024 * 1024):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="compressed_{job_id}.mp4"',
            "Content-Length": str(file_size),
        },
    )
