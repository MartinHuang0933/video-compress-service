# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

非同步影片壓縮微服務。接收 presigned URL 指向的影片，用 FFmpeg 壓縮後上傳回 caller 指定的 S3 presigned URL 與 Ragic，透過 webhook 通知完成。供 LINE 租客約看派工系統呼叫，解決該系統容器資源不足無法壓縮大檔的問題。

## Commands

```bash
# 本地開發（需先安裝 ffmpeg）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

# Docker
docker build -t video-compress .
docker run -p 8080:8080 --env-file .env video-compress

# 健康檢查
curl http://localhost:8080/api/v1/health
```

目前無測試框架。部署平台為 Zeabur（`zeabur.json`）。

## Architecture

```
app/
├── main.py           # FastAPI 入口，註冊路由
├── config.py         # Pydantic Settings 載入環境變數
├── routes/           # HTTP 端點（thin layer，委派給 services）
├── services/
│   ├── compression.py  # 核心：download → ffprobe → ffmpeg → upload → webhook
│   ├── storage.py      # R2/S3 操作（presigned URL、上傳）
│   └── queue.py        # 記憶體內 Job 狀態管理（dict）
├── models/job.py     # Pydantic 模型與 JobStatus 狀態機
└── middleware/auth.py  # X-API-Key 驗證
```

**處理流程**：`POST /api/v1/compress` 回 202 → `asyncio.create_task` 背景執行 `compression.process_job()` → 狀態依序 queued→downloading→compressing→uploading→completed/failed → webhook 通知 caller。

**狀態機**：`JobStatus` enum 定義狀態轉移，每步驟間更新 `queue.update_job_status()`。

## Key Design Decisions

- **Stateless**：服務不存 S3/Ragic 認證，全由 caller 透過 presigned URL 與 ragic_config 傳入
- **Streaming download**：8MB chunks，避免大檔吃光記憶體
- **FFmpeg 透過 subprocess**：`asyncio.subprocess.create_subprocess_exec`，非 ffmpeg-python 函式庫
- **Quality presets**：`QUALITY_PRESETS` dict in `compression.py`（low=CRF28/1280px, medium=CRF23/1920px, high=CRF18/原始）
- **Job 持久化**：v1 用記憶體 dict，重啟即失。caller 端有 reconciliation 機制補償
- **暫存檔清理**：`finally` block 確保 /tmp 檔案清除

## SDD Reference

完整規格文件在 `docs/SDD-影片壓縮服務.md`。程式碼變更應與 SDD 保持一致。

**注意**：目前程式碼與 SDD 有差異尚待同步（見 SDD 中的 Ragic 上傳、webhook 5 次重試、Semaphore 併發控制等）。

## Language

文件與註解使用繁體中文。程式碼（變數名、函式名）使用英文。
