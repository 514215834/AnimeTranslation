"""API 层公共工具：显式装配 JobOut（避免 async 惰性加载 MissingGreenlet）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import PipelineJob, StageRun
from app.schemas.common import JobOut


async def job_to_out(db: AsyncSession, job: PipelineJob) -> JobOut:
    stages = (await db.execute(
        select(StageRun).where(StageRun.job_id == job.id).order_by(StageRun.seq)
    )).scalars().all()
    data = {c.name: getattr(job, c.name) for c in PipelineJob.__table__.columns}
    data["stages"] = stages
    return JobOut.model_validate(data)
