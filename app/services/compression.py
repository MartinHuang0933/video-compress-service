import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.models.job import CompressOptions, CompressResult, Job, JobStatus
from app.services import queue
from app.services.storage import upload_to_forge, upload_to_ragic

logger = logging.getLogger(__name__)

QUALITY_PRESETS = {
    "low": (28, 1280, "96k"),
    "medium": (23, 1920, "128k"),
    "high": (18, None, "192k"),
}

WEBHOOK_RETRY_DELAYS = [5, 30, 120]


async def _probe(file_path: str) -> dict:
    """Get video metadata using ffprobe."""
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    return json.loads(stdout.decode())


def _build_ffmpeg_args(
    input_path: str,
    output_path: str,
    options: CompressOptions,
) -> list[str]:
    """Build ffmpeg command arguments."""
    preset = QUALITY_PRESETS.get(options.quality, QUALITY_PRESETS["medium"])
    crf, max_width, audio_bitrate = preset

    if options.max_width:
        max_width = options.max_width

    args = [
        "ffmpeg",
        "-i", input_path,
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "medium",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        "-y",
    ]

    if max_width:
        args += ["-vf", f"scale='min({max_width},iw)':-2"]

    args.append(output_path)
    return args


async def _download_file(url: str, dest_path: str) -> int:
    """Download a file using streaming. Returns file size in bytes."""
    max_size = settings.max_file_size_mb * 1024 * 1024
    total_size = 0

    async with httpx.AsyncClient(follow_redirects=True, timeout=600) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8 * 1024 * 1024):
                    total_size += len(chunk)
                    if total_size > max_size:
                        raise ValueError(
                            f"File too large: >{settings.max_file_size_mb}MB"
                        )
                    f.write(chunk)
    return total_size


async def _download_and_assemble_chunks(
    urls: list[str], dest_path: str, job_id: str
) -> int:
    """依序下載多個 chunk URL，串流寫入同一檔案。回傳總位元組數。"""
    max_size = settings.max_file_size_mb * 1024 * 1024
    total_size = 0

    with open(dest_path, "wb") as f:
        for idx, url in enumerate(urls, start=1):
            logger.info(
                f"[{job_id}] Downloading chunk {idx}/{len(urls)}"
            )
            chunk_bytes = 0
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=300
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    async for data in response.aiter_bytes(
                        chunk_size=8 * 1024 * 1024
                    ):
                        total_size += len(data)
                        chunk_bytes += len(data)
                        if total_size > max_size:
                            raise ValueError(
                                f"File too large: >{settings.max_file_size_mb}MB"
                            )
                        f.write(data)
            logger.info(
                f"[{job_id}] Chunk {idx}/{len(urls)} done: "
                f"{round(chunk_bytes / (1024 * 1024), 2)}MB "
                f"(total so far: {round(total_size / (1024 * 1024), 2)}MB)"
            )

    logger.info(
        f"[{job_id}] All {len(urls)} chunks assembled: "
        f"{round(total_size / (1024 * 1024), 2)}MB"
    )
    return total_size


async def _send_webhook(webhook_url: str, job: Job) -> None:
    """Send job result to webhook URL with retries (5s → 30s → 120s)."""
    payload = {
        "job_id": job.job_id,
        "status": job.status.value,
        "result": job.result.model_dump() if job.result else None,
        "error": job.error,
        "metadata": job.metadata,
        "original_url": getattr(job, "original_url", None),
    }
    max_attempts = len(WEBHOOK_RETRY_DELAYS) + 1
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(max_attempts):
            try:
                resp = await client.post(webhook_url, json=payload)
                resp.raise_for_status()
                logger.info(f"[{job.job_id}] Webhook sent successfully")
                return
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    logger.error(
                        f"[{job.job_id}] Webhook failed with client error "
                        f"{e.response.status_code}, not retrying"
                    )
                    return
                logger.warning(
                    f"[{job.job_id}] Webhook attempt {attempt + 1}/{max_attempts} "
                    f"failed: {e}"
                )
            except Exception as e:
                logger.warning(
                    f"[{job.job_id}] Webhook attempt {attempt + 1}/{max_attempts} "
                    f"failed: {e}"
                )
            if attempt < len(WEBHOOK_RETRY_DELAYS):
                await asyncio.sleep(WEBHOOK_RETRY_DELAYS[attempt])
    logger.error(
        f"[{job.job_id}] webhook_delivery_failed after {max_attempts} attempts"
    )


async def process_job(job: Job) -> None:
    """Main compression pipeline: download → probe → compress → upload ragic → notify."""
    semaphore = queue.get_semaphore()

    async with semaphore:
        temp_dir = settings.temp_dir
        os.makedirs(temp_dir, exist_ok=True)

        job_id = job.job_id
        input_path = os.path.join(temp_dir, f"{job_id}_input.mp4")
        output_path = os.path.join(temp_dir, f"{job_id}_output.mp4")

        try:
            # Step 1: Download
            queue.update_job_status(job_id, JobStatus.downloading)
            if job.source_urls:
                logger.info(
                    f"[{job_id}] Downloading {len(job.source_urls)} chunks"
                )
                original_size = await _download_and_assemble_chunks(
                    job.source_urls, input_path, job_id
                )
                queue.update_job_status(job_id, JobStatus.assembling)
                logger.info(f"[{job_id}] Chunks assembled successfully")
            else:
                logger.info(f"[{job_id}] Downloading from {job.source_url}")
                original_size = await _download_file(job.source_url, input_path)

            # Set original_url — keep input file as fallback
            original_download_url = (
                f"{settings.base_url.rstrip('/')}/api/v1/jobs/{job_id}/original"
                if settings.base_url
                else f"/api/v1/jobs/{job_id}/original"
            )
            job.original_url = original_download_url
            job.original_path = input_path
            job.original_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            logger.info(f"[{job_id}] Original file available at {original_download_url}")

            # Step 2: Probe
            probe_data = await _probe(input_path)
            duration = float(probe_data.get("format", {}).get("duration", 0))
            video_stream = next(
                (s for s in probe_data.get("streams", []) if s["codec_type"] == "video"),
                None,
            )
            resolution = (
                f"{video_stream['width']}x{video_stream['height']}"
                if video_stream
                else None
            )

            # Step 3: Compress (or skip)
            should_compress = True
            if job.skip_compress is not None:
                should_compress = not job.skip_compress
            elif settings.skip_compression:
                should_compress = False

            if should_compress:
                queue.update_job_status(job_id, JobStatus.compressing)
                ffmpeg_args = _build_ffmpeg_args(input_path, output_path, job.options)
                logger.info(f"[{job_id}] Compressing: {' '.join(ffmpeg_args)}")

                process = await asyncio.create_subprocess_exec(
                    *ffmpeg_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await process.communicate()

                if process.returncode != 0:
                    raise RuntimeError(f"ffmpeg failed: {stderr.decode()[-500:]}")

                compressed_size = os.path.getsize(output_path)
                final_path = output_path
            else:
                logger.info(f"[{job_id}] Skipping compression (skip_compress=True)")
                compressed_size = original_size
                final_path = input_path

            # Step 4: Upload to Ragic (if configured; failure doesn't break the job)
            queue.update_job_status(job_id, JobStatus.uploading)
            ragic_url = None
            ragic_error = None

            if job.ragic_config:
                try:
                    ragic_url = await upload_to_ragic(
                        file_path=final_path,
                        api_url=job.ragic_config.api_url,
                        api_key=job.ragic_config.api_key,
                        form_path=job.ragic_config.form_path,
                        record_id=job.ragic_config.record_id,
                        field_id=job.ragic_config.field_id,
                    )
                except Exception as e:
                    logger.error(f"[{job_id}] Ragic upload failed: {e}")
                    ragic_error = str(e)

            # Step 4.5: Upload to Forge S3 (if configured; failure doesn't break the job)
            forge_url = None
            forge_error = None

            if job.forge_config:
                try:
                    forge_url = await upload_to_forge(
                        file_path=final_path,
                        api_url=job.forge_config.api_url,
                        api_key=job.forge_config.api_key,
                        upload_path=job.forge_config.upload_path,
                    )
                    logger.info(f"[{job_id}] Uploaded to Forge S3: {forge_url[:80]}...")
                except Exception as e:
                    logger.error(f"[{job_id}] Forge upload failed: {e}")
                    forge_error = str(e)

            # Step 5: Complete — keep output file for download
            download_url = (
                f"{settings.base_url.rstrip('/')}/api/v1/jobs/{job_id}/download"
                if settings.base_url
                else f"/api/v1/jobs/{job_id}/download"
            )

            expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=settings.file_retention_minutes
            )

            result = CompressResult(
                download_url=download_url,
                forge_url=forge_url,
                ragic_url=ragic_url if ragic_url else None,
                ragic_error=ragic_error,
                forge_error=forge_error,
                original_size_mb=round(original_size / (1024 * 1024), 2),
                compressed_size_mb=round(compressed_size / (1024 * 1024), 2),
                compression_ratio=(
                    round(compressed_size / original_size, 4) if original_size else 0
                ),
                duration_seconds=round(duration, 2) if duration else None,
                resolution=resolution,
            )

            queue.update_job_status(job_id, JobStatus.completed, result=result)

            # Save output file info for download endpoint
            updated_job = queue.get_job(job_id)
            updated_job.output_path = final_path
            updated_job.output_expires_at = expires_at

            logger.info(
                f"[{job_id}] Done: {result.original_size_mb}MB → "
                f"{result.compressed_size_mb}MB ({result.compression_ratio:.0%})"
            )

            # Step 6: Webhook
            if job.webhook_url:
                await _send_webhook(job.webhook_url, updated_job)

        except Exception as e:
            logger.error(f"[{job_id}] Failed: {e}")
            queue.update_job_status(job_id, JobStatus.failed, error=str(e))
            if job.webhook_url:
                failed_job = queue.get_job(job_id)
                await _send_webhook(job.webhook_url, failed_job)
            # On failure, clean up output file (but not if same as input)
            if output_path != input_path and os.path.exists(output_path):
                os.remove(output_path)
        finally:
            # Only clean up input file if original_url was NOT set
            # (original file is kept for 24h as fallback)
            if not job.original_url and os.path.exists(input_path):
                os.remove(input_path)
