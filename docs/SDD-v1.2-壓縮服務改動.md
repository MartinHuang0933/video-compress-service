# SDD — v1.2 壓縮服務改動（Chunk Assembly 支援）

> 版本：1.2 | 日期：2026-03-31
> 前置文件：`SDD-v1.1-壓縮服務改動.md`

---

## 1. 變更原因

v1.1 架構中，Manus 的 `POST /api/upload/complete-multipart` 在一個 HTTP request 內執行：

1. 從 Forge S3 依序下載所有 chunks（20MB × 10-20 個）
2. 組裝成單一檔案
3. 上傳組裝後檔案回 Forge S3（200-400MB）
4. 呼叫壓縮服務

合計 90-180+ 秒，**超過 Cloudflare 的 ~100 秒 request timeout**，導致 503 Service Unavailable。

**解決方向**：將 chunk 下載 + 組裝移入壓縮服務。Manus 只需產生各 chunk 的下載 URL 並傳給壓縮服務（<5 秒完成 request）。

---

## 2. 修正後的處理流程

```
Manus Server                    壓縮服務                  Forge S3         Ragic
     |                              |                        |               |
     |  [產生 chunk 下載 URLs ~2s]   |                        |               |
     |                              |                        |               |
     |── POST /api/v1/compress ────→|                        |               |
     |   {source_urls: [url0,       |                        |               |
     |    url1, ...urlN],           |                        |               |
     |    webhook_url,              |                        |               |
     |    ragic_config}             |                        |               |
     |←── 202 {job_id} ────────────|                        |               |
     |                              |                        |               |
     |  [HTTP request 結束]         |── GET chunk-0 ────────→|               |
     |                              |←── streaming ──────────|               |
     |                              |── GET chunk-1 ────────→|               |
     |                              |←── streaming ──────────|               |
     |                              |── GET chunk-N ────────→|               |
     |                              |←── streaming ──────────|               |
     |                              |                        |               |
     |                              |   [ffprobe → ffmpeg]   |               |
     |                              |                        |               |
     |                              |── POST ragic API ──────────────────────→|
     |                              |                        |               |
     |←── POST webhook ────────────|                        |               |
     |   {download_url, sizes}      |                        |               |
     |                              |                        |               |
     |── GET /download ────────────→|                        |               |
     |←── streaming 壓縮檔 ─────────|                        |               |
     |── POST /v1/storage/upload ───────────────────────────→|               |
```

### 與 v1.1 的差異

| 步驟 | v1.1 | v1.2 |
|------|------|------|
| 壓縮服務輸入 | 單一 `source_url`（Manus 組裝後的完整檔案） | `source_urls`（chunk 下載 URL 陣列） |
| Chunk 組裝位置 | Manus server | 壓縮服務 |
| Manus complete-multipart 耗時 | 90-180+ 秒（超時 503） | <5 秒 |
| 壓縮服務 pipeline | download → compress | download chunks → assemble → compress |

---

## 3. API 變更

### 3.1 POST /api/v1/compress（修改）

**Request（v1.2）：**
```json
{
  "source_urls": [
    "https://forge-cdn.example.com/signed/chunks/abc/chunk-0?token=...",
    "https://forge-cdn.example.com/signed/chunks/abc/chunk-1?token=...",
    "https://forge-cdn.example.com/signed/chunks/abc/chunk-2?token=..."
  ],
  "webhook_url": "https://lineviewing-5efvppiy.manus.space/api/upload/compress-done",
  "options": {
    "quality": "medium",
    "max_width": 1920,
    "format": "mp4"
  },
  "ragic_config": {
    "api_url": "https://ap13.ragic.com",
    "api_key": "...",
    "form_path": "/OnePlaceLiving/development-department/2",
    "record_id": "2921",
    "field_id": "1007590"
  },
  "metadata": {
    "upload_job_id": "original-upload-job-id",
    "task_id": "viewing-task-123"
  }
}
```

**與 v1.1 差異：**

- 新增 `source_urls: list[str]` — 有序的 chunk 下載 URL 陣列
- `source_url` 改為 Optional — 仍可使用（向後相容）
- 驗證規則：必須提供 `source_url` 或 `source_urls` 其中一個，不可同時提供

**驗證錯誤回應：**

| 條件 | body |
|------|------|
| 兩者皆未提供 | `{"detail": "Either source_url or source_urls must be provided"}` |
| 同時提供兩者 | `{"detail": "Provide source_url OR source_urls, not both"}` |
| `source_urls` 為空陣列 | `{"detail": "source_urls must not be empty"}` |

### 3.2 其他端點 — 不變

| 端點 | 變更 |
|------|------|
| `GET /api/v1/jobs/{job_id}` | 不變（status 新增 `assembling` 值） |
| `GET /api/v1/jobs/{job_id}/download` | 不變 |
| `GET /api/v1/health` | 不變 |
| Webhook payload | 不變 |

---

## 4. 資料模型變更

### CompressRequest

```python
class CompressRequest(BaseModel):
    source_url: Optional[str] = None       # 單一 URL（向後相容）
    source_urls: Optional[list[str]] = None # 有序 chunk URL 陣列（新增）
    webhook_url: Optional[str] = None
    options: CompressOptions = CompressOptions()
    ragic_config: Optional[RagicConfig] = None
    metadata: Optional[dict] = None

    @model_validator(mode="after")
    def validate_source(self):
        if not self.source_url and not self.source_urls:
            raise ValueError("Either source_url or source_urls must be provided")
        if self.source_url and self.source_urls:
            raise ValueError("Provide source_url OR source_urls, not both")
        if self.source_urls is not None and len(self.source_urls) == 0:
            raise ValueError("source_urls must not be empty")
        return self
```

### JobStatus

```python
class JobStatus(str, Enum):
    queued = "queued"
    downloading = "downloading"
    assembling = "assembling"      # 新增：chunks 下載完成，標記組裝完成
    compressing = "compressing"
    uploading = "uploading"
    completed = "completed"
    failed = "failed"
```

### Job

```python
class Job(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.queued
    source_url: Optional[str] = None        # 改為 Optional
    source_urls: Optional[list[str]] = None  # 新增
    webhook_url: Optional[str] = None
    options: CompressOptions = CompressOptions()
    ragic_config: Optional[RagicConfig] = None
    metadata: Optional[dict] = None
    result: Optional[CompressResult] = None
    error: Optional[str] = None
    output_path: Optional[str] = None
    output_expires_at: Optional[datetime] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
```

### CompressResult — 不變

---

## 5. 壓縮流程變更

```python
async def process_job(job):
    # Step 1: 下載（修改）
    status → downloading

    if job.source_urls:
        # Multi-chunk 模式：依序下載各 chunk，串流寫入同一檔案
        for i, url in enumerate(source_urls):
            streaming GET url → append to input_path (8MB chunks)
            累計檢查 max_file_size_mb
        status → assembling  # 標記組裝完成
    else:
        # 單一 URL 模式：不變
        streaming GET source_url → input_path

    # Step 2: 探測 — 不變
    ffprobe → duration, resolution

    # Step 3: 壓縮 — 不變
    status → compressing
    ffmpeg 壓縮

    # Step 4: Ragic 上傳 — 不變
    status → uploading

    # Step 5: 完成 — 不變
    記錄 output_path, download_url
    status → completed

    # Step 6: Webhook — 不變

    # Step 7: 清理 — 不變
    finally: 刪除 input 暫存檔
```

### 新增函式：`_download_and_assemble_chunks`

```python
async def _download_and_assemble_chunks(
    urls: list[str],
    dest_path: str,
    job_id: str,
) -> int:
    """依序下載 chunk URLs 並組裝成單一檔案。回傳 total bytes。"""
    max_size = settings.max_file_size_mb * 1024 * 1024
    total_size = 0

    with open(dest_path, "wb") as f:
        for i, url in enumerate(urls):
            async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=8 * 1024 * 1024):
                        total_size += len(chunk)
                        if total_size > max_size:
                            raise ValueError(f"File too large: >{settings.max_file_size_mb}MB")
                        f.write(chunk)

    return total_size
```

**設計要點：**
- 依序下載，直接 append 到同一檔案（不產生中間 chunk 檔案）
- 8MB streaming chunks，記憶體友善
- `max_file_size_mb` 檢查累計大小（整個組裝後檔案）
- 單個 chunk 下載 timeout 300 秒（20MB chunk 應綽綽有餘）

---

## 6. 向後相容性

| 呼叫方式 | 行為 |
|---------|------|
| `{"source_url": "https://..."}` | 與 v1.1 完全相同 |
| `{"source_urls": ["url0", "url1"]}` | 下載 + 組裝 + 壓縮 |
| 同時提供兩者 | 422 驗證錯誤 |
| 兩者皆空 | 422 驗證錯誤 |

`assembling` 狀態僅在 multi-chunk 模式出現。單一 URL 模式的狀態流程不變。

---

## 7. 注意事項

### Chunk URL 有效期

Forge S3 的下載 URL 為臨時簽名 URL，有過期時間。壓縮服務應在收到 job 後盡快開始下載。目前 `max_concurrent_jobs=2`，佇列等待時間通常不超過數分鐘。

### 磁碟空間

Multi-chunk 模式下，同時存在 input（原始組裝檔）+ output（壓縮後檔案）：
- 400MB 原始 + ~85MB 壓縮 ≈ 485MB per job
- 2 個併發 job ≈ ~1GB
- Zeabur 容器通常有 10GB+ 暫存空間，足夠使用

### Chunk 順序

`source_urls` 陣列的順序即為組裝順序（chunk-0, chunk-1, ..., chunk-N）。壓縮服務按陣列索引依序下載寫入。

---

## 8. 程式碼修改清單

| 檔案 | 改動 |
|------|------|
| `app/models/job.py` | `CompressRequest` 新增 `source_urls`、`source_url` 改 Optional、model_validator；`Job` 新增 `source_urls`、`source_url` 改 Optional；`JobStatus` 新增 `assembling` |
| `app/services/compression.py` | 新增 `_download_and_assemble_chunks()`；`process_job()` 加入 multi-chunk 分支 |
| `app/routes/compress.py` | `submit_compress_job` 傳遞 `source_urls` 到 Job |

---

## 9. 端點總覽（v1.2）

| 方法 | 路徑 | 說明 | 變更 |
|------|------|------|------|
| POST | `/api/v1/compress` | 提交壓縮任務 | 新增 `source_urls` 支援 |
| GET | `/api/v1/jobs/{job_id}` | 查詢任務狀態 | status 新增 `assembling` 值 |
| GET | `/api/v1/jobs/{job_id}/download` | 下載壓縮後檔案 | 不變 |
| GET | `/api/v1/health` | 健康檢查 | 不變 |

---

## 10. 驗證方式

```bash
# 1. 單一 URL（向後相容測試）
curl -X POST https://video-compress-service.zeabur.app/api/v1/compress \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <KEY>" \
  -d '{
    "source_url": "https://d2xsxph8kpxj0f.cloudfront.net/...",
    "options": {"quality": "medium"}
  }'
# 預期：202，job 完成後可下載

# 2. Multi-chunk URLs
curl -X POST https://video-compress-service.zeabur.app/api/v1/compress \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <KEY>" \
  -d '{
    "source_urls": [
      "https://chunk-url-0...",
      "https://chunk-url-1...",
      "https://chunk-url-2..."
    ],
    "options": {"quality": "medium"}
  }'
# 預期：202，job 狀態經過 downloading → assembling → compressing → completed

# 3. 驗證錯誤處理
curl -X POST https://video-compress-service.zeabur.app/api/v1/compress \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <KEY>" \
  -d '{"options": {"quality": "medium"}}'
# 預期：422，缺少 source_url 和 source_urls

# 4. 下載壓縮檔
curl -o compressed.mp4 \
  https://video-compress-service.zeabur.app/api/v1/jobs/{job_id}/download \
  -H "X-API-Key: <KEY>"
# 預期：200，檔案可播放
```
