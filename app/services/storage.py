import base64
import logging

import httpx

logger = logging.getLogger(__name__)


async def upload_to_s3_presigned(presigned_url: str, file_path: str) -> None:
    """Upload a file to S3 using a presigned PUT URL."""
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.put(
            presigned_url,
            content=file_bytes,
            headers={"Content-Type": "video/mp4"},
        )
        resp.raise_for_status()
    logger.info(f"Uploaded to S3 presigned URL ({len(file_bytes)} bytes)")


async def upload_to_ragic(
    file_path: str,
    api_url: str,
    api_key: str,
    form_path: str,
    record_id: str,
    field_id: str,
) -> str:
    """Upload a file to Ragic as an attachment. Returns the file URL from Ragic response."""
    url = f"{api_url.rstrip('/')}{form_path}/{record_id}"
    auth_header = f"Basic {base64.b64encode(api_key.encode()).decode()}"

    with open(file_path, "rb") as f:
        files = {"file": (f"compressed_{record_id}.mp4", f, "video/mp4")}
        data = {"field_id": field_id}

        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                url,
                headers={"Authorization": auth_header},
                files=files,
                data=data,
            )
            resp.raise_for_status()

    result = resp.json()
    logger.info(f"Uploaded to Ragic: {url}")
    return result.get("file_url", "")
