# SDD — 影片壓縮服務

> 版本：1.0 | 日期：2026-03-29

---

## 1. 概述

### 1.1 目的

提供獨立的影片壓縮微服務，供外部系統（如 LINE 租客約看派工系統）透過 API 提交影片壓縮任務。服務從 S3/CloudFront 下載原始影片，使用 ffmpeg 壓縮，並將結果同時存回 Manus S3（播放用）與 Ragic（歸檔用）。

### 1.2 背景

LINE 約看系統的 Production 環境（Manus 託管容器）存在以下限制：
- 容器記憶體 512MB–2GB，無法本地壓縮大型影片
- `/tmp` 為暫存檔案系統，容器重啟後清除
- Cloudflare proxy 100MB body limit、~100s timeout
- 現有系統上傳 47MB 影片時 `/api/upload/complete` 即發生 524 timeout

因此需要獨立的壓縮服務來處理影片壓縮，徹底將大檔案處理從 LINE 系統中卸載。

### 1.3 範圍

本文件僅涵蓋**壓縮服務**的設計規格。LINE 系統的改動規格請參閱 `line-viewing-dispatch/docs/SDD-影片上傳流程改動.md`。

---

## 2. 系統架構

### 2.1 部署架構

| 元件 | 部署位置 | 用途 |
|------|---------|------|
| 壓縮服務 | Zeabur（Docker 容器） | API 伺服器 + ffmpeg 壓縮 |
| Manus S3 | AWS S3 + CloudFront CDN | 影片儲存與播放 |
| Ragic | ap13.ragic.com | 影片歸檔，與案件資料綁定 |

### 2.2 處理流程

```
LINE 伺服器                  壓縮服務                    Manus S3         Ragic
     |                          |                          |               |
     |── POST /api/v1/compress →|                          |               |
     |   {source_url,           |                          |               |
     |    webhook_url,          |                          |               |
     |    ragic_config,         |                          |               |
     |    manus_upload_url}     |                          |               |
     |←── 202 {job_id} ────────|                          |               |
     |                          |                          |               |
     |                          |── GET source_url ───────→|               |
     |                          |←── streaming 下載 ───────|               |
     |                          |                          |               |
     |                          |   [ffprobe 取得資訊]      |               |
     |                          |   [ffmpeg 壓縮]           |               |
     |                          |                          |               |
     |                          |── PUT manus_upload_url ──→|               |
     |                          |   (presigned URL)        |               |
     |                          |                          |               |
     |                          |── POST ragic API ────────────────────────→|
     |                          |   (上傳壓縮後檔案)        |               |
     |                          |                          |               |
     |←── POST webhook ────────|                          |               |
     |   {job_id, status,       |                          |               |
     |    compressed_s3_url,    |                          |               |
     |    ragic_url, sizes}     |                          |               |
```

### 2.3 並發控制

使用 `asyncio.Semaphore` 限制同時壓縮任務數量，超出上限的任務排隊等待。

| 設定 | 值 | 說明 |
|------|-----|------|
| `MAX_CONCURRENT_JOBS` | 2 | 同時最多 2 個 ffmpeg 進程 |
| 超出上限 | 排隊等待 | 狀態為 `queued`，不會被丟棄 |

---

## 3. API 規格

### 3.1 認證

所有端點（`/health` 除外）需要在 Header 中帶上 API Key：

```
X-API-Key: <API_KEY>
```

### 3.2 端點列表

| 方法 | 路徑 | 說明 | 認證 |
|------|------|------|------|
| POST | `/api/v1/compress` | 提交壓縮任務 | 需要 |
| GET | `/api/v1/jobs/{job_id}` | 查詢任務狀態 | 需要 |
| GET | `/api/v1/health` | 健康檢查 | 不需要 |

---

### 3.3 POST /api/v1/compress

提交一個壓縮任務。立即回傳 `job_id`，壓縮在背景執行。

#### Request

```json
{
  "source_url": "https://d2xsxph8kpxj0f.cloudfront.net/uploads/abc123.mp4",
  "webhook_url": "https://lineviewing-5efvppiy.manus.space/api/upload/compress-done",
  "manus_upload_url": "https://s3.amazonaws.com/...presigned-put-url...",
  "options": {
    "quality": "medium",
    "max_width": 1920,
    "format": "mp4"
  },
  "ragic_config": {
    "api_url": "https://ap13.ragic.com",
    "api_key": "...",
    "form_path": "/oneplaceliving/forms/12",
    "record_id": "456",
    "field_id": "1000789"
  },
  "metadata": {
    "upload_job_id": "original-upload-job-id",
    "task_id": "viewing-task-123"
  }
}
```

| 欄位 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `source_url` | string | 是 | 原始影片的 CloudFront URL |
| `webhook_url` | string | 否 | 壓縮完成後的通知 URL |
| `manus_upload_url` | string | 否 | Manus S3 presigned PUT URL（存回 S3 用） |
| `options.quality` | string | 否 | `low` / `medium`（預設）/ `high` |
| `options.max_width` | int | 否 | 最大寬度（px），預設依品質決定 |
| `options.format` | string | 否 | 輸出格式，預設 `mp4` |
| `ragic_config` | object | 否 | Ragic 上傳設定。提供時壓縮後會上傳到 Ragic |
| `ragic_config.api_url` | string | 條件 | Ragic API 基礎 URL |
| `ragic_config.api_key` | string | 條件 | Ragic API Key |
| `ragic_config.form_path` | string | 條件 | Ragic 表單路徑 |
| `ragic_config.record_id` | string | 條件 | Ragic 記錄 ID |
| `ragic_config.field_id` | string | 條件 | Ragic 欄位 ID（檔案附件欄位） |
| `metadata` | object | 否 | 任意附帶資料，原封不動帶在 webhook 回傳 |

#### Response — 202 Accepted

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued"
}
```

---

### 3.4 GET /api/v1/jobs/{job_id}

查詢任務狀態。前端 polling 用。

#### Response — 200 OK

任務進行中：
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "compressing",
  "result": null,
  "error": null,
  "created_at": "2026-03-29T10:00:00Z",
  "completed_at": null
}
```

任務完成：
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "completed",
  "result": {
    "compressed_s3_url": "https://d2xsxph8kpxj0f.cloudfront.net/uploads/compressed-abc.mp4",
    "ragic_url": "https://ap13.ragic.com/sims/file.jsp?...",
    "original_size_mb": 450.2,
    "compressed_size_mb": 85.3,
    "compression_ratio": 0.19,
    "duration_seconds": 142.5,
    "resolution": "1920x1080"
  },
  "error": null,
  "created_at": "2026-03-29T10:00:00Z",
  "completed_at": "2026-03-29T10:03:45Z"
}
```

任務失敗：
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "failed",
  "result": null,
  "error": "ffmpeg failed: ...",
  "created_at": "2026-03-29T10:00:00Z",
  "completed_at": "2026-03-29T10:01:12Z"
}
```

#### Response — 404 Not Found

```json
{
  "detail": "Job not found"
}
```

---

### 3.5 GET /api/v1/health

```json
{
  "status": "ok"
}
```

---

### 3.6 Webhook 回傳格式

壓縮完成（成功或失敗）後，壓縮服務會 POST 到 `webhook_url`：

```json
{
  "job_id": "a1b2c3d4-...",
  "status": "completed",
  "result": {
    "compressed_s3_url": "https://d2xsxph8kpxj0f.cloudfront.net/uploads/compressed-abc.mp4",
    "ragic_url": "https://ap13.ragic.com/sims/file.jsp?...",
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

#### Webhook 重試策略

| 次數 | 延遲 | 累計時間 |
|------|------|---------|
| 第 1 次 | 立即 | 0s |
| 第 2 次 | 10s | 10s |
| 第 3 次 | 30s | 40s |
| 第 4 次 | 90s | 130s |
| 第 5 次 | 270s | 400s (~6.5 分鐘) |

5 次全部失敗後停止重試。LINE 伺服器可透過定時補撈機制（GET `/api/v1/jobs/{job_id}`）取回結果。

---

## 4. 資料模型

### 4.1 JobStatus 狀態機

```
queued → downloading → compressing → uploading → completed
                                                ↗
  任何階段失敗 ──────────────────────→ failed
```

| 狀態 | 說明 |
|------|------|
| `queued` | 任務已建立，等待處理（可能在排隊） |
| `downloading` | 正在從 CloudFront 下載原始檔 |
| `compressing` | ffmpeg 壓縮中 |
| `uploading` | 上傳壓縮後檔案到 S3 和 Ragic |
| `completed` | 全部完成 |
| `failed` | 任何階段失敗，`error` 欄位有錯誤訊息 |

### 4.2 任務儲存

v1 使用記憶體內 dict 儲存任務狀態。服務重啟後任務遺失，但因為：
- LINE 伺服器的定時補撈會發現未完成的任務並重新提交
- 壓縮任務是冪等的（同一個 source_url 重壓不會造成問題）

所以 v1 不需要持久化儲存。

---

## 5. 壓縮策略

### 5.1 ffmpeg 參數

| 品質 | CRF | 最大寬度 | 音訊 | 預期壓縮率 | 適用場景 |
|------|-----|---------|------|-----------|---------|
| `low` | 28 | 1280px | AAC 96kbps | 10-20% | 省空間、行動裝置 |
| `medium` | 23 | 1920px | AAC 128kbps | 20-35% | 一般用途（預設） |
| `high` | 18 | 原始 | AAC 192kbps | 40-60% | 高品質保留 |

### 5.2 固定參數

```
-c:v libx264          # H.264 編碼，相容性最高
-preset medium        # 編碼速度與品質平衡
-movflags +faststart  # MP4 metadata 前置，支援瀏覽器邊下載邊播放
-y                    # 覆蓋輸出檔
```

### 5.3 縮放規則

```
-vf scale='min({max_width},iw)':-2
```

只會縮小，不會放大。寬度不超過 `max_width`，高度自動等比例計算（`-2` 確保為偶數）。

### 5.4 壓縮效益判斷

如果壓縮後大小 >= 原始大小的 95%，視為壓縮無效，仍使用壓縮後檔案（因為 `-movflags +faststart` 的串流化仍有價值）。

---

## 6. 上傳目標

### 6.1 上傳到 Manus S3

壓縮服務收到的 `manus_upload_url` 是 LINE 伺服器預先產生的 S3 presigned PUT URL。壓縮服務直接用 HTTP PUT 上傳壓縮後檔案：

```
PUT {manus_upload_url}
Content-Type: video/mp4
Body: <compressed file bytes>
```

不需要 Manus API 憑證——presigned URL 本身就包含認證。

### 6.2 上傳到 Ragic

若 `ragic_config` 有提供，壓縮完成後透過 Ragic API 上傳檔案附件：

```
POST {ragic_config.api_url}{ragic_config.form_path}/{ragic_config.record_id}
Authorization: Basic {base64(ragic_config.api_key)}
Content-Type: multipart/form-data

file: <compressed video file>
field_id: {ragic_config.field_id}
```

### 6.3 上傳順序

1. 先上傳 S3（播放用，優先確保使用者能看到結果）
2. 再上傳 Ragic（歸檔用，失敗不影響主流程）
3. Ragic 上傳失敗時：任務仍標記為 `completed`，但 `ragic_url` 為 `null`，webhook 中帶上 `ragic_error` 欄位

---

## 7. 處理管線詳細流程

```python
async def process_job(job):
    # 1. 下載
    status → downloading
    streaming 下載到 {TEMP_DIR}/{job_id}_input.mp4（8MB chunks，不吃記憶體）

    # 2. 探測
    ffprobe 取得 duration、resolution、codec 資訊

    # 3. 壓縮
    status → compressing
    ffmpeg 壓縮到 {TEMP_DIR}/{job_id}_output.mp4
    失敗時擷取 stderr 最後 500 字元作為錯誤訊息

    # 4. 上傳
    status → uploading
    PUT 壓縮檔到 manus_upload_url（S3 presigned URL）
    POST 壓縮檔到 Ragic（若有設定）

    # 5. 完成
    status → completed
    組裝 CompressResult

    # 6. 通知
    POST webhook（5 次重試）

    # 7. 清理
    finally: 刪除 input + output 暫存檔
```

---

## 8. 專案結構

```
影片壓縮/
├── docs/
│   └── SDD-影片壓縮服務.md      # 本文件
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 入口，路由註冊
│   ├── config.py                # 環境變數載入（Pydantic Settings）
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── compress.py          # POST /compress, GET /jobs/{id}
│   │   └── health.py            # GET /health
│   ├── services/
│   │   ├── __init__.py
│   │   ├── compression.py       # ffmpeg 壓縮管線（下載→壓縮→上傳→通知）
│   │   ├── storage.py           # S3 presigned URL 上傳 + Ragic 上傳
│   │   └── queue.py             # 任務佇列管理（記憶體 dict + Semaphore）
│   ├── models/
│   │   ├── __init__.py
│   │   └── job.py               # Pydantic 資料模型
│   └── middleware/
│       ├── __init__.py
│       └── auth.py              # API Key 驗證
├── .env.example
├── .gitignore
├── Dockerfile
├── zeabur.json
└── requirements.txt
```

---

## 9. 環境變數

```bash
# 伺服器
PORT=8080
API_KEY=your-secret-api-key

# 壓縮設定
DEFAULT_QUALITY=medium
MAX_FILE_SIZE_MB=1000
MAX_CONCURRENT_JOBS=2
TEMP_DIR=/tmp/video-compress
```

注意：壓縮服務**不需要** S3 或 Ragic 的憑證。
- S3 上傳使用 LINE 伺服器提供的 presigned URL
- Ragic 上傳使用 LINE 伺服器提供的 `ragic_config`

這樣壓縮服務是完全無狀態的，不與任何外部帳號耦合。

---

## 10. 部署

### 10.1 Dockerfile

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 10.2 zeabur.json

```json
{
  "build": {
    "type": "dockerfile",
    "dockerfile": "Dockerfile"
  },
  "start": {
    "command": "uvicorn app.main:app --host 0.0.0.0 --port 8080"
  }
}
```

### 10.3 dependencies（requirements.txt）

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic-settings==2.7.1
httpx==0.28.1
```

注意：不再需要 `boto3`。S3 上傳改用 presigned URL（純 HTTP PUT），Ragic 上傳用 `httpx`。

---

## 11. 錯誤處理

| 階段 | 錯誤 | 處理方式 |
|------|------|---------|
| 下載 | CloudFront URL 無法存取 | 重試 3 次（指數退避），失敗後 status → `failed` |
| 下載 | 檔案超過 `MAX_FILE_SIZE_MB` | 立即 status → `failed`，error: "File too large" |
| 壓縮 | ffmpeg 錯誤 | 擷取 stderr 最後 500 字元，status → `failed` |
| 上傳 S3 | presigned URL 過期或無效 | status → `failed`，error 帶上 HTTP 狀態碼 |
| 上傳 Ragic | Ragic API 錯誤 | **不影響主流程**，compressed_s3_url 仍回傳，ragic_url 為 null |
| Webhook | LINE 伺服器無回應 | 重試 5 次（10s/30s/90s/270s/810s），全失敗後停止 |
| 暫存檔 | 磁碟空間不足 | status → `failed`，error: "Disk full" |

所有失敗狀態都會觸發 webhook 通知（如有設定），讓 LINE 伺服器知道任務失敗。

---

## 12. 安全性

| 項目 | 措施 |
|------|------|
| API 認證 | `X-API-Key` header，與環境變數 `API_KEY` 比對 |
| 檔案驗證 | ffprobe 驗證檔案為有效影片格式 |
| 暫存清理 | `finally` 區塊確保暫存檔案必定刪除 |
| 記憶體安全 | streaming 下載（8MB chunks），不將影片載入記憶體 |
| 無憑證儲存 | 不儲存 S3/Ragic 憑證，使用呼叫端提供的 presigned URL 和 ragic_config |

---

## 13. 驗證方式

### 13.1 本地測試

```bash
# 1. 建置 Docker 映像
docker build -t video-compress .

# 2. 啟動服務
docker run -p 8080:8080 -e API_KEY=test123 video-compress

# 3. 健康檢查
curl http://localhost:8080/api/v1/health

# 4. 提交壓縮任務（用公開影片 URL 測試）
curl -X POST http://localhost:8080/api/v1/compress \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test123" \
  -d '{
    "source_url": "https://d2xsxph8kpxj0f.cloudfront.net/test-video.mp4",
    "options": {"quality": "medium"}
  }'

# 5. 查詢狀態
curl http://localhost:8080/api/v1/jobs/{job_id} \
  -H "X-API-Key: test123"
```

### 13.2 整合測試

1. LINE 系統上傳影片 → 取得 CloudFront URL
2. LINE 系統呼叫 `POST /compress` → 取得 `job_id`
3. 前端 polling `GET /jobs/{job_id}` → 確認狀態變化正確
4. 確認 webhook 送達 LINE 伺服器
5. 確認壓縮後影片可透過 CloudFront URL 播放
6. 確認 Ragic 記錄中有壓縮後的檔案附件
7. 確認暫存檔已清理（`/tmp/video-compress/` 為空）
