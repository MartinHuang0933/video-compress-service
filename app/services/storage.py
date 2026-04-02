import base64
import logging

import httpx

logger = logging.getLogger(__name__)


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


async def upload_to_forge(
    file_path: str,
    api_url: str,
    api_key: str,
    upload_path: str,
) -> str:
    """Upload a file to Forge S3 storage. Returns the public URL."""
    upload_url = f"{api_url.rstrip('/')}/v1/storage/upload"

    with open(file_path, "rb") as f:
        files = {"file": (upload_path.split("/")[-1], f, "video/mp4")}

        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                upload_url,
                params={"path": upload_path},
                headers={"Authorization": f"Bearer {api_key}"},
                files=files,
            )
            resp.raise_for_status()

    result = resp.json()
    url = result.get("url", "")
    logger.info(f"Uploaded to Forge: {url[:80]}...")
    return url
