"""LLM 渠道管理 API（F-M1-04）：OpenAI 兼容渠道 CRUD + 连通性测试。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.db.session import get_db
from app.models.entities import LLMChannel
from app.providers.llm import ChannelConfig, LLMError, LLMProvider
from app.schemas.common import ChannelCreate, ChannelOut, ChannelTestOut, ChannelUpdate

router = APIRouter(prefix="/llm/channels", tags=["llm-channels"])


def _to_out(c: LLMChannel) -> ChannelOut:
    out = ChannelOut.model_validate(c)
    try:
        out.api_key_masked = security.mask(security.decrypt(c.api_key_enc))
    except security.CryptoError:
        out.api_key_masked = "(解密失败，请重新保存)"
    return out


@router.get("", response_model=list[ChannelOut])
async def list_channels(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(LLMChannel).order_by(LLMChannel.created_at))).scalars().all()
    return [_to_out(c) for c in rows]


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(body: ChannelCreate, db: AsyncSession = Depends(get_db)):
    c = LLMChannel(
        **body.model_dump(exclude={"api_key"}),
        api_key_enc=security.encrypt(body.api_key),
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return _to_out(c)


@router.put("/{channel_id}", response_model=ChannelOut)
async def update_channel(channel_id: str, body: ChannelUpdate, db: AsyncSession = Depends(get_db)):
    c = await db.get(LLMChannel, channel_id)
    if not c:
        raise HTTPException(404, "渠道不存在")
    data = body.model_dump(exclude_none=True)
    api_key = data.pop("api_key", None)
    for k, v in data.items():
        setattr(c, k, v)
    if api_key:  # 空串=不修改
        c.api_key_enc = security.encrypt(api_key)
    await db.commit()
    await db.refresh(c)
    return _to_out(c)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(channel_id: str, db: AsyncSession = Depends(get_db)):
    c = await db.get(LLMChannel, channel_id)
    if not c:
        raise HTTPException(404, "渠道不存在")
    await db.delete(c)
    await db.commit()


@router.post("/{channel_id}/test", response_model=ChannelTestOut)
async def test_channel(channel_id: str, db: AsyncSession = Depends(get_db)):
    """连通性测试：发送一条最小翻译请求（F-M1-04 渠道验证）。

    永不抛 500：任何失败都折算成 ok=False + 可读原因，前端直接展示。
    """
    c = await db.get(LLMChannel, channel_id)
    if not c:
        raise HTTPException(404, "渠道不存在")
    start = time.monotonic()
    provider: LLMProvider | None = None
    try:
        api_key = security.decrypt(c.api_key_enc)
        provider = LLMProvider(ChannelConfig(
            id=c.id, name=c.name, base_url=c.base_url,
            api_key=api_key, model=c.model,
            temperature=0.0, timeout_seconds=30.0,
            extra_headers=c.extra_headers or {},
        ))
        resp = await provider.chat(
            [{"role": "user", "content": '将"おはよう"翻译为简体中文，输出 JSON：{"zh": "..."}'}],
            json_mode=True, max_retries=1, max_tokens=1024,  # 推理模型的思考也计 max_tokens，给足余量
        )
        return ChannelTestOut(ok=True, message="连接成功",
                              latency_ms=int((time.monotonic() - start) * 1000),
                              reply=resp.content[:200])
    except security.CryptoError as e:
        return ChannelTestOut(ok=False, message=f"API Key 解密失败：{e}（请重新编辑保存一次 Key）",
                              latency_ms=int((time.monotonic() - start) * 1000))
    except LLMError as e:
        return ChannelTestOut(ok=False, message=str(e),
                              latency_ms=int((time.monotonic() - start) * 1000))
    except Exception as e:  # noqa: BLE001 — 代理故障/SSL/编码等意外错误也不 500
        return ChannelTestOut(ok=False, message=f"连接异常：{e}",
                              latency_ms=int((time.monotonic() - start) * 1000))
    finally:
        if provider is not None:
            await provider.aclose()
