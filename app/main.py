import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from app.config import settings
from app.routes import compress, health
from app.services.queue import _jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


async def _cleanup_expired_files():
    """Periodically clean up expired output files."""
    while True:
        await asyncio.sleep(600)  # every 10 minutes
        now = datetime.now(timezone.utc)
        cleaned = 0
        for job_id, job in list(_jobs.items()):
            if (
                job.output_expires_at
                and now > job.output_expires_at
                and job.output_path
                and os.path.exists(job.output_path)
            ):
                os.remove(job.output_path)
                job.output_path = None
                cleaned += 1
        if cleaned:
            logger.info(f"Cleaned up {cleaned} expired file(s)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_expired_files())
    yield
    task.cancel()


app = FastAPI(
    title="Video Compression Service",
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(compress.router)
