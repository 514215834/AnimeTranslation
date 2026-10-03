# AnimeTranslation 后端 API 镜像（含 FFmpeg 与 faster-whisper 运行依赖）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/data/models/hf

# FFmpeg（探测/抽轨/压制都收敛在此）
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖再拷代码，利用层缓存
COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic pydantic-settings \
    "sqlalchemy>=2.0" aiosqlite httpx python-multipart loguru pysubs2 cryptography \
    python-dotenv "faster-whisper>=1.0" pytest pytest-asyncio

COPY app ./app
COPY tests ./tests

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
