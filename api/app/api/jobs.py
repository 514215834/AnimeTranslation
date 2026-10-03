"""流水线任务 API（F-M1-07）：进度查询、单 Stage 重试、取消。创建入口在 POST /episodes/{id}/jobs。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.entities import PipelineJob
from app.api.utils import job_to_out
from app.schemas.common import JobOut, RetryRequest
from app.services.pipeline import engine
from app.services.pipeline.engine import STAGE_ORDER, job_control

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(PipelineJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return await job_to_out(db, job)


@router.get("", response_model=list[JobOut])
async def list_jobs(episode_id: str | None = None, limit: int = 50,
                    db: AsyncSession = Depends(get_db)):
    q = select(PipelineJob).order_by(PipelineJob.created_at.desc()).limit(limit)
    if episode_id:
        q = q.where(PipelineJob.episode_id == episode_id)
    return [await job_to_out(db, j) for j in (await db.execute(q)).scalars().all()]


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry(job_id: str, body: RetryRequest, db: AsyncSession = Depends(get_db)):
    if body.stage not in STAGE_ORDER:
        raise HTTPException(422, f"未知 Stage：{body.stage}（可选 {STAGE_ORDER}）")
    try:
        await engine.retry_stage(job_id, body.stage)
    except engine.PipelineError as e:
        raise HTTPException(409, str(e))
    job = await db.get(PipelineJob, job_id)
    return await job_to_out(db, job)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(PipelineJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.state in ("queued", "running"):
        job_control.request_cancel(job_id)
        job.state = "canceled"
        await db.commit()
    return await job_to_out(db, job)
