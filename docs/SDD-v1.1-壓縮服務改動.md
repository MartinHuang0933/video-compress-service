# SDD — v1.1 壓縮服務改動

> 版本：1.1 | 日期：2026-03-30
> 前置文件：`SDD-影片壓縮服務.md` v1.0

---

## 1. 變更原因

v1.0 設計中，壓縮服務收到 `manus_upload_url`（S3 presigned PUT URL）後，直接 PUT 壓縮檔到 Manus S3。

**實測發現 Forge API 不支援 presigned PUT URL。** Forge Storage API 只提供：

| 端點 | 方法 | 用途 |
|------|------|------|
| `/v1/storage/upload` | POST multipart/form-data | 上傳檔案 |
| `/v1/storage/downloadUrl` | GET | 取得下載 URL |
| `/v1/storage/delete` | DELETE | 刪除檔案 |

壓縮服務用 PUT 打 Forge upload 端點，收到 404。

**解決方向**：壓縮服務不再負責上傳到 S3。改為提供**下載端點**，由 LINE 伺服器自行下載壓縮檔後透過 Forge API 上傳。

---

## 2. 修正後的處理流程

```
LINE 伺服器                  壓縮服務                    Manus S3         Ragic
     |                          |                          |               |
     |── POST /api/v1/compress →|                          |               |
     |   {source_url,           |                          |               |
     |    webhook_url,          |                          |               |
     |    ragic_config}         |                          |               |
     |←── 202 {job_id} ────────|                          |               |
     |                          |                          |               |
     |                          |── GET source_url ───────→|               |
     |                          |←── streaming 下載 ───────|               |
     |                          |                          |               |
     |                          |   [ffprobe → ffmpeg 壓縮] |               |
     |                          |                          |               |
     |                          |── POST ragic API ────────────────────────→|
     |                          |   (上傳壓縮後檔案)        |               |
     |                          |                          |               |
     |←── POST webhook ────────|                          |               |
     |   {download_url,         |                          |               |
     |    ragic_url, sizes}     |                          |               |
     |                          |                          |               |
     |── GET /download ────────→|                          |               |
     |←── streaming 壓縮檔 ─────|                          |               |
     |                          |                          |               |
     |── POST /v1/storage/upload ─────────────────────────→|               |
     |←── {S3 URL} ────────────────────────────────────── |               |
```

### 與 v1.0 的差異

| 步驟 | v1.0 | v1.1 |
|------|------|------|
| 壓縮完成後上傳 S3 | 壓縮服務 PUT `manus_upload_url` | 壓縮服務不上傳，提供下載端點 |
| Webhook 回傳 | `compressed_s3_url` | `download_url` |
| 壓縮服務需要的參數 | `manus_upload_url` | 不需要（已移除） |
| 壓縮後暫存檔 | 立即刪除 | 保留 1 小時供下載 |

---

## 3. API 變更

### 3.1 移除

| 項目 | 說明 |
|------|------|
| `CompressRequest.manus_upload_url` | 壓縮服務不上傳到 S3 |
| `CompressResult.compressed_s3_url` | 壓縮服務不知道 S3 URL |
| `storage.upload_to_s3_presigned()` | 不再需要 |

### 3.2 新增

| 項目 | 說明 |
|------|------|
| `GET /api/v1/jobs/{job_id}/download` | 下載壓縮後的檔案 |
| `CompressResult.download_url` | 壓縮檔的下載 URL |
| 暫存檔保留機制 | 壓縮後檔案保留 1 小時 |
| 背景清理任務 | 定期清除過期暫存檔 |

---

### 3.3 POST /api/v1/compress（修改）

**Request（v1.1）：**
```json
{
  "source_url": "https://d2xsxph8kpxj0f.cloudfront.net/uploads/abc123.mp4",
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

**與 v1.0 差異：移除 `manus_upload_url` 欄位。**

---

### 3.4 GET /api/v1/jobs/{job_id}/download（新增）

下載壓縮後的影片檔案。

**Request：**
```
GET /api/v1/jobs/{job_id}/download
X-API-Key: <API_KEY>
```

**Response — 200 OK：**
```
Content-Type: video/mp4
Content-Disposition: attachment; filename="compressed_{job_id}.mp4"
Content-Length: <file size>

<binary video data, streaming>
```

**錯誤回應：**

| 狀態碼 | 條件 | body |
|--------|------|------|
| 404 | job 不存在 | `{"detail": "Job not found"}` |
| 400 | job 未完成 | `{"detail": "Job not completed"}` |
| 410 | 檔案已過期清除 | `{"detail": "File expired and has been cleaned up"}` |

**行為說明：**
- 只有 `status = completed` 的 job 可以下載
- 使用 `StreamingResponse` 避免大檔案吃記憶體
- 檔案保留 1 小時，過期後回傳 410
- 需要 `X-API-Key` 認證

---

### 3.5 GET /api/v1/jobs/{job_id}（修改）

**Response — 任務完成：**
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "completed",
  "result": {
    "download_url": "https://video-compress-service.zeabur.app/api/v1/jobs/a1b2c3d4-.../download",
    "ragic_url": "https://ap13.ragic.com/sims/file.jsp?...",
    "ragic_error": null,
    "original_size_mb": 450.2,
    "compressed_size_mb": 85.3,
    "compression_ratio": 0.19,
    "duration_seconds": 142.5,
    "resolution": "1920x1080"
  },
  "error": null,
  "created_at": "2026-03-30T10:00:00Z",
  "completed_at": "2026-03-30T10:03:45Z"
}
```

**與 v1.0 差異：`compressed_s3_url` → `download_url`。**

---

### 3.6 Webhook payload（修改）

```json
{
  "job_id": "a1b2c3d4-...",
  "status": "completed",
  "result": {
    "download_url": "https://video-compress-service.zeabur.app/api/v1/jobs/a1b2c3d4-.../download",
    "ragic_url": "https://ap13.ragic.com/sims/file.jsp?...",
    "ragic_error": null,
    "original_size_mb": 450.2,
    "compressed_size_mb": 85.3,
    "compression_ratio": 0.19,
    "duration_seconds": 142.5,
    "resolution": "1920x1080"
  },
  "error": null,
  "metadata": {
    "upload_job_id": "original-upload-job-id",
    "task_id": "viewing-task-123"
  }
}
```

---

## 4. 資料模型變更

### CompressRequest

```python
class CompressRequest(BaseModel):
    source_url: str
    webhook_url: Optional[str] = None
    # manus_upload_url: 已移除
    options: CompressOptions = CompressOptions()
    ragic_config: Optional[RagicConfig] = None
    metadata: Optional[dict] = None
```

### CompressResult

```python
class CompressResult(BaseModel):
    download_url: str                        # 新增
    # compressed_s3_url: 已移除
    ragic_url: Optional[str] = None
    ragic_error: Optional[str] = None
    original_size_mb: float
    compressed_size_mb: float
    compression_ratio: float
    duration_seconds: Optional[float] = None
    resolution: Optional[str] = None
```

### Job

```python
class Job(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.queued
    source_url: str
    webhook_url: Optional[str] = None
    # manus_upload_url: 已移除
    options: CompressOptions = CompressOptions()
    ragic_config: Optional[RagicConfig] = None
    metadata: Optional[dict] = None
    result: Optional[CompressResult] = None
    error: Optional[str] = None
    output_path: Optional[str] = None            # 新增：壓縮後檔案路徑
    output_expires_at: Optional[datetime] = None  # 新增：檔案過期時間
    created_at: datetime
    completed_at: Optional[datetime] = None
```

`output_path` 和 `output_expires_at` 為內部欄位，**不會出現在 API response 中**。

---

## 5. 壓縮流程變更

```python
async def process_job(job):
    # Step 1-3: 下載、探測、壓縮 — 不變

    # Step 4: 上傳（簡化）
    status → uploading
    # 4a: 上傳到 S3 — 已移除
    # 4b: 上傳到 Ragic — 不變（若有 ragic_config）

    # Step 5: 完成（修改）
    記錄 output_path（不刪除壓縮後檔案）
    設定 output_expires_at = now + FILE_RETENTION_MINUTES
    生成 download_url = f"{BASE_URL}/api/v1/jobs/{job_id}/download"
    status → completed

    # Step 6: Webhook — 不變（payload 包含 download_url）

    # Step 7: 清理（修改）
    finally: 只刪除 input 暫存檔，output 保留供下載
```

---

## 6. 暫存檔清理機制

壓縮後的 output 檔案保留 `FILE_RETENTION_MINUTES`（預設 60 分鐘），超過後自動清除。

```python
async def cleanup_expired_files():
    """每 10 分鐘執行，清除過期的壓縮檔。"""
    for job_id, job in _jobs.items():
        if (job.output_expires_at
            and datetime.now(timezone.utc) > job.output_expires_at
            and job.output_path
            and os.path.exists(job.output_path)):
            os.remove(job.output_path)
            job.output_path = None
```

啟動方式：FastAPI `lifespan` 事件中以 `asyncio.create_task` 啟動背景循環。

---

## 7. 新增環境變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `BASE_URL` | `""` | 服務的外部可達 URL，用於組裝 `download_url` |
| `FILE_RETENTION_MINUTES` | `60` | 壓縮檔保留時間（分鐘） |

---

## 8. 程式碼修改清單

| 檔案 | 改動 |
|------|------|
| `app/models/job.py` | 移除 `manus_upload_url`；`CompressResult` 改用 `download_url`；`Job` 加 `output_path`、`output_expires_at` |
| `app/config.py` | 新增 `base_url`、`file_retention_minutes` |
| `app/routes/compress.py` | 移除 `manus_upload_url` 傳遞；新增 `GET /download` 端點 |
| `app/services/compression.py` | 移除 S3 上傳邏輯；保留 output 檔案；生成 `download_url` |
| `app/services/storage.py` | 移除 `upload_to_s3_presigned()` |
| `app/main.py` | 新增 lifespan 啟動清理任務 |
| `.env.example` | 新增 `BASE_URL`、`FILE_RETENTION_MINUTES` |

---

## 9. 端點總覽（v1.1）

| 方法 | 路徑 | 說明 | 認證 | 變更 |
|------|------|------|------|------|
| POST | `/api/v1/compress` | 提交壓縮任務 | 需要 | 移除 `manus_upload_url` |
| GET | `/api/v1/jobs/{job_id}` | 查詢任務狀態 | 需要 | `compressed_s3_url` → `download_url` |
| **GET** | **`/api/v1/jobs/{job_id}/download`** | **下載壓縮後檔案** | **需要** | **新增** |
| GET | `/api/v1/health` | 健康檢查 | 不需要 | 不變 |

---

## 10. 驗證方式

```bash
# 1. 提交壓縮任務
curl -X POST https://video-compress-service.zeabur.app/api/v1/compress \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <KEY>" \
  -d '{
    "source_url": "https://d2xsxph8kpxj0f.cloudfront.net/310519663374214772/5EFvppiYZyDqEy7dvyxV7i/chat-uploads/d094a2d1-aa21-4c70-994c-c31149a00bd2.mp4",
    "options": {"quality": "medium"}
  }'

# 2. 查詢狀態（等壓縮完成）
curl https://video-compress-service.zeabur.app/api/v1/jobs/{job_id} \
  -H "X-API-Key: <KEY>"
# 確認 result.download_url 有值

# 3. 下載壓縮後檔案
curl -o compressed.mp4 \
  https://video-compress-service.zeabur.app/api/v1/jobs/{job_id}/download \
  -H "X-API-Key: <KEY>"
# 確認檔案可播放且大小合理

# 4. 1 小時後再次下載
# 預期回傳 410 Gone
```
