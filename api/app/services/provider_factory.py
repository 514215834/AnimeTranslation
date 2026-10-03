"""Provider 工厂：按配置构建 ASR / LLM Provider，加载项目术语表。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.models.entities import GlossaryTerm, LLMChannel, PipelineJob, Project
from app.providers.asr import ASRProvider
from app.providers.llm import ChannelConfig, LLMProvider


def build_asr_provider(model_size: str = "small") -> ASRProvider:
    """M1 内置 faster-whisper；Provider 选择后续从 project.pipeline_config.asr_provider 读取。

    设备/量化/beam 由环境变量控制（api/.env）：
    ASR_DEVICE / ASR_COMPUTE_TYPE / ASR_BEAM_SIZE —— 1050 Ti 等 4GB Pascal 卡默认 int8。
    """
    from app.config import get_settings

    s = get_settings()
    return ASRProvider(
        model_size=model_size,
        device=s.asr_device,
        models_dir=s.resolved_models_dir,
        compute_type=s.asr_compute_type,
        beam_size=s.asr_beam_size,
    )


async def _default_channel(session: AsyncSession) -> LLMChannel | None:
    res = await session.execute(
        select(LLMChannel).where(LLMChannel.enabled.is_(True)).order_by(LLMChannel.created_at)
    )
    return res.scalars().first()


async def build_llm_provider(session: AsyncSession, job: PipelineJob) -> LLMProvider:
    """按 Job 配置中的 channel_id 构建 LLM Provider；未指定时取第一个启用渠道。"""
    cfg = job.config or {}
    channel: LLMChannel | None = None
    if cfg.get("channel_id"):
        channel = await session.get(LLMChannel, cfg["channel_id"])
        if channel and not channel.enabled:
            channel = None
    if channel is None:
        channel = await _default_channel(session)
    if channel is None:
        from app.services.pipeline.engine import PipelineError

        raise PipelineError(
            "没有可用的翻译渠道：请先在「翻译渠道」页面添加 OpenAI 兼容渠道（base_url / api_key / model）"
        )
    try:
        api_key = security.decrypt(channel.api_key_enc)
    except security.CryptoError as e:
        from app.services.pipeline.engine import PipelineError

        raise PipelineError(
            f"渠道[{channel.name}] API Key 解密失败：{e}（请在「翻译渠道」页重新编辑保存一次 Key）"
        ) from e
    cc = ChannelConfig(
        id=channel.id,
        name=channel.name,
        base_url=channel.base_url,
        api_key=api_key,
        model=channel.model,
        temperature=channel.temperature,
        rpm_limit=channel.rpm_limit,
        extra_headers=channel.extra_headers or {},
    )
    return LLMProvider(cc)


async def load_project_terms(session: AsyncSession, project_id: str) -> list[dict]:
    """全局术语 + 项目术语（F-M1-05）。"""
    res = await session.execute(
        select(GlossaryTerm).where(
            (GlossaryTerm.project_id == project_id) | (GlossaryTerm.project_id.is_(None))
        )
    )
    return [
        {
            "source": t.source,
            "target": t.target,
            "aliases": t.aliases or [],
            "locked": bool(t.locked),
        }
        for t in res.scalars().all()
    ]
