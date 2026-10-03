"""FastAPI 入口：路由装配、静态文件（媒体预览 + 前端构建产物）、启动初始化。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api import episodes, glossary, jobs, llm_channels, projects, segments, settings as settings_api
from app.config import get_settings
from app.db.session import dispose_engine, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logger.info(f"{s.app_name} v{s.version} 启动 | data={s.data_dir} | ffmpeg={s.ffmpeg_path}")
    await init_db()
    # 启动时将上次中断的 running 任务标记为 failed（断点续跑：产物保留，可单 Stage 重试）
    from sqlalchemy import update

    from app.db.session import get_session_factory
    from app.models.entities import PipelineJob

    async with get_session_factory()() as session:
        await session.execute(
            update(PipelineJob).where(PipelineJob.state.in_(["running", "queued"]))
            .values(state="failed", error="服务重启导致中断，请重试失败 Stage（已成功 Stage 不会重跑）")
        )
        await session.commit()
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version=s.version, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects.router, prefix=s.api_prefix)
    app.include_router(episodes.router, prefix=s.api_prefix)
    app.include_router(jobs.router, prefix=s.api_prefix)
    app.include_router(segments.router, prefix=s.api_prefix)
    app.include_router(glossary.router, prefix=s.api_prefix)
    app.include_router(llm_channels.router, prefix=s.api_prefix)
    app.include_router(settings_api.router, prefix=s.api_prefix)

    @app.get(f"{s.api_prefix}/health")
    async def health():
        return {"status": "ok", "version": s.version,
                "ffmpeg": s.ffmpeg_path, "ffprobe": s.ffprobe_path}

    # 上传视频的本地静态服务（前端 <video> 预览）
    app.mount("/media", StaticFiles(directory=str(s.media_dir)), name="media")

    # 生产模式：托管前端构建产物（web/dist），SPA fallback
    dist = s.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            from fastapi.responses import FileResponse

            target = dist / full_path
            if full_path and target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
