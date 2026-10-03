"""剧集管理 + 视频上传（F-M1-01）。

上传即探测：FFprobe 解析时长/分辨率/音轨并回写（验收：上传 24min 视频后 10s 内显示元信息）。
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.models.entities import Episode, PipelineJob, Project, Segment
from app.api.utils import job_to_out
from app.schemas.common import EpisodeCreate, EpisodeOut, EpisodeUpdate, JobCreate, JobOut
from app.services import media
from app.services.pipeline import engine
from app.services.pipeline.engine import STAGE_ORDER

router = APIRouter(prefix="/episodes", tags=["episodes"])


async def _to_out(db: AsyncSession, ep: Episode) -> EpisodeOut:
    out = EpisodeOut.model_validate(ep)
    out.segment_count = await db.scalar(
        select(func.count(Segment.id)).where(Segment.episode_id == ep.id)) or 0
    out.translated_count = await db.scalar(
        select(func.count(Segment.id)).where(Segment.episode_id == ep.id,
                                             Segment.target_text != "")) or 0
    return out


async def _get_episode_or_404(db: AsyncSession, episode_id: str) -> Episode:
    ep = await db.get(Episode, episode_id)
    if not ep:
        raise HTTPException(404, "剧集不存在")
    return ep


@router.get("", response_model=list[EpisodeOut])
async def list_episodes(project_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Episode).where(Episode.project_id == project_id).order_by(Episode.number)
    )).scalars().all()
    return [await _to_out(db, e) for e in rows]


@router.post("", response_model=EpisodeOut, status_code=201)
async def create_episode(project_id: str, body: EpisodeCreate, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    dup = await db.scalar(select(Episode.id).where(Episode.project_id == project_id,
                                                   Episode.number == body.number))
    if dup:
        raise HTTPException(409, f"第 {body.number} 集已存在")
    ep = Episode(project_id=project_id, number=body.number, title=body.title)
    db.add(ep)
    await db.commit()
    await db.refresh(ep)
    return await _to_out(db, ep)


@router.put("/{episode_id}", response_model=EpisodeOut)
async def update_episode(episode_id: str, body: EpisodeUpdate, db: AsyncSession = Depends(get_db)):
    ep = await _get_episode_or_404(db, episode_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(ep, k, v)
    await db.commit()
    await db.refresh(ep)
    return await _to_out(db, ep)


@router.delete("/{episode_id}", status_code=204)
async def delete_episode(episode_id: str, db: AsyncSession = Depends(get_db)):
    ep = await _get_episode_or_404(db, episode_id)
    await db.delete(ep)
    await db.commit()


@router.post("/{episode_id}/video", response_model=EpisodeOut)
async def upload_video(episode_id: str, file: UploadFile, db: AsyncSession = Depends(get_db)):
    """上传本地视频（mp4/mkv 等），流式落盘后 FFprobe 探测元信息。"""
    settings = get_settings()
    ep = await _get_episode_or_404(db, episode_id)

    ext = Path(file.filename or "").suffix.lower()
    if ext not in settings.allowed_upload_exts:
        raise HTTPException(415, f"不支持的文件类型 {ext or '(无后缀)'}，允许：{' '.join(settings.allowed_upload_exts)}")

    ep_dir = settings.media_dir / ep.id
    ep_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.\-\u4e00-\u9fff\u3040-\u30ff\u4e00-\u9fff]+", "_",
                       Path(file.filename or "video").stem)[:120] or "video"
    dst = ep_dir / f"{safe_name}_{uuid.uuid4().hex[:8]}{ext}"

    size = 0
    limit = settings.upload_max_mb * 1024 * 1024
    try:
        with dst.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"文件超过 {settings.upload_max_mb}MB 上限")
                f.write(chunk)
    except HTTPException:
        dst.unlink(missing_ok=True)
        raise
    except OSError as e:
        raise HTTPException(500, f"写入文件失败：{e}")

    # 探测元信息（失败则保留文件并标记 error，报错信息可见）
    ep.video_path = str(dst.relative_to(settings.data_dir))
    ep.video_name = file.filename or dst.name
    try:
        info = await media.probe(str(dst))
        ep.duration_ms = info.duration_ms
        ep.width = info.width
        ep.height = info.height
        ep.audio_tracks = info.audio_tracks
        ep.status = "ready"
        ep.error = ""
    except media.MediaError as e:
        ep.status = "error"
        ep.error = str(e)
        await db.commit()
        await db.refresh(ep)
        raise HTTPException(422, str(e))
    await db.commit()
    await db.refresh(ep)
    return await _to_out(db, ep)


@router.post("/{episode_id}/jobs", response_model=JobOut, status_code=201)
async def create_episode_job(episode_id: str, body: JobCreate, db: AsyncSession = Depends(get_db)):
    """对单集启动流水线（F-M1-07）。可指定 Stage 子集实现断点重跑。"""
    ep = await _get_episode_or_404(db, episode_id)
    if not ep.video_path:
        raise HTTPException(409, "请先上传视频再启动任务")
    stages = [s for s in (body.stages or STAGE_ORDER) if s in STAGE_ORDER]
    if not stages:
        raise HTTPException(422, f"stages 无效，可选：{STAGE_ORDER}")
    job = PipelineJob(
        episode_id=episode_id,
        config={
            "stages": stages,
            "asr_model": body.asr_model,
            "asr_language": body.asr_language,
            "channel_id": body.channel_id,
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    engine.enqueue_job(job.id, from_stage=stages[0])
    return await job_to_out(db, job)


@router.get("/{episode_id}/jobs", response_model=list[JobOut])
async def list_episode_jobs(episode_id: str, db: AsyncSession = Depends(get_db)):
    ep = await _get_episode_or_404(db, episode_id)
    rows = (await db.execute(
        select(PipelineJob).where(PipelineJob.episode_id == ep.id)
        .order_by(PipelineJob.created_at.desc())
    )).scalars().all()
    return [await job_to_out(db, j) for j in rows]


@router.get("/{episode_id}/exports")
async def list_exports(episode_id: str, db: AsyncSession = Depends(get_db)):
    """列出该集已生成的导出成品（文件名/大小/下载路径）。"""
    ep = await _get_episode_or_404(db, episode_id)
    out_dir = get_settings().exports_dir / ep.id
    files = []
    if out_dir.exists():
        for f in sorted(out_dir.iterdir()):
            if f.is_file() and f.suffix == ".srt":
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "url": f"{get_settings().api_prefix}/episodes/{episode_id}/exports/{f.name}",
                })
    return files


@router.get("/{episode_id}/exports/{filename}")
async def download_export(episode_id: str, filename: str, db: AsyncSession = Depends(get_db)):
    """下载导出成品（SRT 等），文件名白名单校验防目录穿越。"""
    from fastapi.responses import FileResponse

    ep = await _get_episode_or_404(db, episode_id)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    path = get_settings().exports_dir / ep.id / filename
    if not path.exists():
        raise HTTPException(404, "导出文件不存在，请先跑完 subtitle/export Stage")
    media_type = "application/x-subrip" if filename.endswith(".srt") else "application/octet-stream"
    return FileResponse(path, filename=filename, media_type=media_type)


@router.get("/{episode_id}/video")
async def stream_video(episode_id: str, db: AsyncSession = Depends(get_db)):
    """前端预览视频用（302 到静态文件路由）。"""
    from fastapi.responses import RedirectResponse

    ep = await _get_episode_or_404(db, episode_id)
    if not ep.video_path:
        raise HTTPException(404, "尚未上传视频")
    return RedirectResponse(f"/media/{ep.video_path.removeprefix('media/')}")


@router.get("/{episode_id}/jobs/latest", response_model=None)
async def latest_job(episode_id: str, db: AsyncSession = Depends(get_db)):
    ep = await _get_episode_or_404(db, episode_id)
    job = (await db.execute(
        select(PipelineJob).where(PipelineJob.episode_id == ep.id)
        .order_by(PipelineJob.created_at.desc()).limit(1)
    )).scalars().first()
    return await job_to_out(db, job) if job else None
