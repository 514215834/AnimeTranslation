"""字幕行 API（F-M1-08 字幕预览/编辑；M3 复核工作台数据源）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.entities import Episode, Segment
from app.schemas.common import SegmentOut, SegmentUpdate

router = APIRouter(prefix="/episodes", tags=["segments"])

ALLOWED_STATUS = {"draft", "translated", "reviewed", "final"}


async def _get_episode_or_404(db: AsyncSession, episode_id: str) -> Episode:
    ep = await db.get(Episode, episode_id)
    if not ep:
        raise HTTPException(404, "剧集不存在")
    return ep


@router.get("/{episode_id}/segments", response_model=list[SegmentOut])
async def list_segments(episode_id: str, db: AsyncSession = Depends(get_db)):
    await _get_episode_or_404(db, episode_id)
    rows = (await db.execute(
        select(Segment).where(Segment.episode_id == episode_id).order_by(Segment.index)
    )).scalars().all()
    return [SegmentOut.model_validate(r) for r in rows]


@router.put("/{episode_id}/segments/{segment_id}", response_model=SegmentOut)
async def update_segment(episode_id: str, segment_id: str, body: SegmentUpdate,
                         db: AsyncSession = Depends(get_db)):
    await _get_episode_or_404(db, episode_id)
    seg = await db.get(Segment, segment_id)
    if not seg or seg.episode_id != episode_id:
        raise HTTPException(404, "字幕行不存在")
    data = body.model_dump(exclude_none=True)
    if "status" in data and data["status"] not in ALLOWED_STATUS:
        raise HTTPException(422, f"status 取值 {ALLOWED_STATUS}")
    for k, v in data.items():
        setattr(seg, k, v)
    # 人工改动译文后视为已复核，并清除翻译失败标记
    if "target_text" in data:
        seg.status = "reviewed" if data["target_text"].strip() else "draft"
        flags = [f for f in (seg.qc_flags or []) if f != "translate_failed"]
        seg.qc_flags = flags
    await db.commit()
    await db.refresh(seg)
    return SegmentOut.model_validate(seg)


@router.delete("/{episode_id}/segments", status_code=204)
async def delete_all_segments(episode_id: str, db: AsyncSession = Depends(get_db)):
    await _get_episode_or_404(db, episode_id)
    rows = (await db.execute(
        select(Segment).where(Segment.episode_id == episode_id)
    )).scalars().all()
    for r in rows:
        await db.delete(r)
    await db.commit()
