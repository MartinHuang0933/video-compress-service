import logging

from fastapi import FastAPI

from app.routes import compress, health

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="Video Compression Service", version="1.0.0")

app.include_router(health.router)
app.include_router(compress.router)
