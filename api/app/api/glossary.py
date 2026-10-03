"""全局/项目术语表 API（F-M1-05）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.entities import GlossaryTerm, Project
from app.schemas.common import GlossaryTermCreate, GlossaryTermOut, GlossaryTermUpdate

router = APIRouter(prefix="/glossary", tags=["glossary"])


@router.get("", response_model=list[GlossaryTermOut])
async def list_terms(project_id: str | None = None, q: str | None = None,
                     db: AsyncSession = Depends(get_db)):
    stmt = select(GlossaryTerm).order_by(GlossaryTerm.created_at.desc())
    if project_id:
        stmt = stmt.where(or_(GlossaryTerm.project_id == project_id,
                              GlossaryTerm.project_id.is_(None)))
    if q:
        stmt = stmt.where(or_(GlossaryTerm.source.ilike(f"%{q}%"),
                              GlossaryTerm.target.ilike(f"%{q}%")))
    rows = (await db.execute(stmt)).scalars().all()
    return [GlossaryTermOut.model_validate(r) for r in rows]


@router.post("", response_model=GlossaryTermOut, status_code=201)
async def create_term(body: GlossaryTermCreate, db: AsyncSession = Depends(get_db)):
    if body.project_id:
        if not await db.get(Project, body.project_id):
            raise HTTPException(404, "项目不存在")
    t = GlossaryTerm(**body.model_dump())
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return GlossaryTermOut.model_validate(t)


@router.put("/{term_id}", response_model=GlossaryTermOut)
async def update_term(term_id: str, body: GlossaryTermUpdate, db: AsyncSession = Depends(get_db)):
    t = await db.get(GlossaryTerm, term_id)
    if not t:
        raise HTTPException(404, "词条不存在")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    await db.commit()
    await db.refresh(t)
    return GlossaryTermOut.model_validate(t)


@router.delete("/{term_id}", status_code=204)
async def delete_term(term_id: str, db: AsyncSession = Depends(get_db)):
    t = await db.get(GlossaryTerm, term_id)
    if not t:
        raise HTTPException(404, "词条不存在")
    await db.delete(t)
    await db.commit()
