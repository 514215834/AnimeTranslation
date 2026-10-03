"""流水线引擎（F-M1-07 / 架构文档 3.1-3.3）。

Stage 状态机：ingest → asr → translate → subtitle → export
- 断点续跑：每个 Stage 产物落盘 data/artifacts/{job_id}/{stage}.json；
  重跑已成功 Stage 直接复用产物。
- 单 Stage 失败只重试该 Stage（+ 下游失效重置）；连续失败 3 次整 Job failed。
- 进程内 asyncio worker：单并发跑 Job（GPU/LLM 重任务，M1 够用）。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session_factory
from app.models.entities import Episode, PipelineJob, Project, Segment, StageRun
from app.providers.asr import ASRError, ASRProvider
from app.services import media
from app.services.srt_export import export_srt_bilingual, export_srt_target
from app.services.translate_engine import TranslateEngine, TranslateResult

STAGE_ORDER = ["ingest", "asr", "translate", "subtitle", "export"]
MAX_STAGE_AUTO_RETRY = 3

# 翻译失败行的兜底占位，导出时跳过；提示人工处理
UNTRANSLATED_PLACEHOLDER = ""


class JobControl:
    """协作式取消：各 Stage 在长循环中检查。"""

    def __init__(self) -> None:
        self._canceled: set[str] = set()

    def request_cancel(self, job_id: str) -> None:
        self._canceled.add(job_id)

    def is_canceled(self, job_id: str) -> bool:
        return job_id in self._canceled

    def clear(self, job_id: str) -> None:
        self._canceled.discard(job_id)


job_control = JobControl()


class PipelineError(RuntimeError):
    pass


# ---------------- 产物存取 ----------------

def artifact_path(job_id: str, stage: str) -> Path:
    p = get_settings().artifacts_dir / job_id / f"{stage}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_artifact(job_id: str, stage: str, payload: dict) -> str:
    p = artifact_path(job_id, stage)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(p.relative_to(get_settings().data_dir))


def load_artifact(job_id: str, stage: str) -> dict | None:
    p = artifact_path(job_id, stage)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def clear_artifacts_from(job_id: str, stage: str) -> None:
    """重置某 Stage 起的所有产物（该 Stage 将重跑）。"""
    idx = STAGE_ORDER.index(stage)
    for st in STAGE_ORDER[idx:]:
        p = artifact_path(job_id, st)
        if p.exists():
            p.unlink()


# ---------------- Stage 实现 ----------------
# 每个函数签名：(session, job, stage_run, ctx) -> dict(产物 payload)
# ctx: {"canceled": Callable[[], bool]}

async def stage_ingest(session: AsyncSession, job: PipelineJob, sr: StageRun, ctx: dict) -> dict:
    ep = await session.get(Episode, job.episode_id)
    if not ep or not ep.video_path:
        raise PipelineError("剧集尚未上传视频文件")
    settings = get_settings()
    video_abs = settings.data_dir / ep.video_path
    if not video_abs.exists():
        raise PipelineError(f"视频文件不存在：{video_abs}")

    # 1) 探测元信息（若剧集已探测过则复用，避免重复 ffprobe）
    if ep.duration_ms and ep.width:
        audio_tracks = ep.audio_tracks or []
    else:
        try:
            info = await media.probe(str(video_abs))
        except media.MediaError as e:
            raise PipelineError(f"媒体探测失败：{e}") from e
        ep.duration_ms = info.duration_ms
        ep.width = info.width
        ep.height = info.height
        audio_tracks = info.audio_tracks
        ep.audio_tracks = info.audio_tracks

    # 2) 抽取 16k 单声道 wav
    audio_rel = Path("media") / ep.id / "audio.wav"
    audio_abs = settings.data_dir / audio_rel
    try:
        await media.extract_audio(str(video_abs), str(audio_abs))
    except media.MediaError as e:
        raise PipelineError(f"音轨抽取失败：{e}") from e

    ep.status = "ready"
    payload = {
        "audio_wav": str(audio_rel.as_posix()),
        "duration_ms": ep.duration_ms,
        "width": ep.width,
        "height": ep.height,
        "audio_tracks": audio_tracks,
    }
    await session.commit()
    return payload


async def stage_asr(session: AsyncSession, job: PipelineJob, sr: StageRun, ctx: dict) -> dict:
    ingest = load_artifact(job.id, "ingest")
    if not ingest:
        raise PipelineError("缺少 ingest 产物（audio.wav），请先跑 ingest Stage")
    audio_abs = get_settings().data_dir / ingest["audio_wav"]
    if not Path(audio_abs).exists():
        raise PipelineError("audio.wav 不存在，请重跑 ingest")

    cfg = job.config or {}
    model_size = cfg.get("asr_model") or "small"
    language = cfg.get("asr_language") or "ja"

    from app.services.provider_factory import build_asr_provider

    provider: ASRProvider = build_asr_provider(model_size=model_size)

    def _transcribe() -> list[dict]:
        lines = provider.transcribe(str(audio_abs), language=language)
        return [
            {
                "index": i + 1,
                "start_ms": max(0, l.start_ms),
                "end_ms": max(l.start_ms + 200, l.end_ms),
                "source_text": l.text.strip(),
                "confidence": float(l.confidence) if l.confidence else 1.0,
            }
            for i, l in enumerate(lines)
            if l.text.strip()
        ]

    # faster-whisper 是同步推理 → 丢线程池，避免阻塞事件循环
    loop = asyncio.get_running_loop()
    try:
        segments = await loop.run_in_executor(None, _transcribe)
    except ASRError:
        raise
    except Exception as e:  # noqa: BLE001
        raise PipelineError(f"ASR 执行失败：{e}") from e
    if not segments:
        raise PipelineError("ASR 未识别出任何台词，请检查音轨语言是否正确")
    if ctx.get("canceled") and ctx["canceled"](job.id):
        raise PipelineError("任务已取消")

    # 行落库：重跑 asr 时覆盖该集全部行（locked 行保留，仅 M3 生效，M1 预留逻辑）
    ep = await session.get(Episode, job.episode_id)
    existing = (await session.execute(
        select(Segment).where(Segment.episode_id == ep.id)
    )).scalars().all()
    kept_locked: dict[int, Segment] = {s.index: s for s in existing if s.locked}
    for s in existing:
        if not s.locked:
            await session.delete(s)
    await session.flush()
    for seg in segments:
        old = kept_locked.get(seg["index"])
        session.add(Segment(
            episode_id=ep.id,
            index=seg["index"],
            start_ms=old.start_ms if old else seg["start_ms"],
            end_ms=old.end_ms if old else seg["end_ms"],
            source_text=old.source_text if old else seg["source_text"],
            confidence=seg["confidence"],
            track_type="dialogue",
            status="draft",
        ))
    await session.commit()
    return {"line_count": len(segments), "language": language, "model": model_size}


async def stage_translate(session: AsyncSession, job: PipelineJob, sr: StageRun, ctx: dict) -> dict:
    from app.services.provider_factory import build_llm_provider, load_project_terms

    ep = await session.get(Episode, job.episode_id)
    project = await session.get(Project, ep.project_id)
    rows = (await session.execute(
        select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.index)
    )).scalars().all()
    if not rows:
        raise PipelineError("没有可翻译的字幕行，请先跑 asr Stage")
    todo = [r for r in rows if not r.locked and r.source_text.strip()]
    if not todo:
        return {"line_count": 0, "note": "全部行已锁定或为空，跳过翻译"}

    terms = await load_project_terms(session, ep.project_id)
    provider: LLMProvider = await build_llm_provider(session, job)
    engine = TranslateEngine(provider, batch_size=get_settings().translate_batch_size,
                             context_lines=get_settings().context_lines)
    try:
        lang_name = {"ja": "日语"}.get(project.source_lang, project.source_lang)
        result: TranslateResult = await engine.translate_lines(
            [{"index": r.index, "source_text": r.source_text} for r in todo],
            terms, source_lang_name=lang_name,
        )
    finally:
        await provider.aclose()

    translated = 0
    for r in todo:
        zh = result.translations.get(r.index)
        if zh is not None:
            r.target_text = zh
            r.status = "translated"
            translated += 1
    for idx in result.failed_indices:
        r = next(x for x in rows if x.index == idx)
        r.target_text = UNTRANSLATED_PLACEHOLDER
        r.status = "draft"
        r.qc_flags = sorted(set((r.qc_flags or []) + ["translate_failed"]))
    await session.commit()

    if result.failed_indices:
        sr.error = f"{len(result.failed_indices)} 行翻译失败，已标记待人工处理"
    usage = {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "cost": round(result.cost, 6),
        "translated_lines": translated,
        "failed_lines": len(result.failed_indices),
    }
    prev = dict(job.token_cost_json or {})
    for k in ("prompt_tokens", "completion_tokens"):
        prev[k] = prev.get(k, 0) + usage[k]
    prev["cost"] = round(prev.get("cost", 0.0) + usage["cost"], 6)
    job.token_cost_json = prev
    await session.commit()
    return usage


async def stage_subtitle(session: AsyncSession, job: PipelineJob, sr: StageRun, ctx: dict) -> dict:
    """生成 SRT 产物（data/exports/{episode_id}/）。双语与单语两份。"""
    ep = await session.get(Episode, job.episode_id)
    rows = (await session.execute(
        select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.index)
    )).scalars().all()
    if not rows:
        raise PipelineError("没有字幕行可生成字幕文件")
    seg_dicts = [
        {
            "index": r.index, "start_ms": r.start_ms, "end_ms": r.end_ms,
            "source_text": r.source_text, "target_text": r.target_text,
        }
        for r in rows
    ]
    settings = get_settings()
    out_dir = settings.exports_dir / ep.id
    single = out_dir / f"E{ep.number:02d}.zh.srt"
    bi = out_dir / f"E{ep.number:02d}.bilingual.srt"
    export_srt_target(seg_dicts, str(single))
    export_srt_bilingual(seg_dicts, str(bi))
    payload = {
        "srt_target": single.name,
        "srt_bilingual": bi.name,
        "export_dir": str(out_dir.relative_to(settings.data_dir)),
        "line_count": len(seg_dicts),
        "translated_count": sum(1 for s in seg_dicts if s["target_text"].strip()),
    }
    await session.commit()
    return payload


async def stage_export(session: AsyncSession, job: PipelineJob, sr: StageRun, ctx: dict) -> dict:
    """交付：校验产物完整性并登记下载信息（M1 = SRT；ASS/MKV/MP4 在 M2/M3）。

    单语文件为空（没有任何已翻译行）不算失败——降级为 warning，
    双语文件仍含原文可下载；只有文件完全缺失（subtitle 没产出）才失败。
    """
    sub = load_artifact(job.id, "subtitle")
    if not sub:
        raise PipelineError("缺少 subtitle 产物")
    settings = get_settings()
    export_dir = settings.data_dir / sub["export_dir"]
    files = []
    warnings = []
    for key in ("srt_target", "srt_bilingual"):
        f = export_dir / sub[key]
        if not f.exists():
            raise PipelineError(f"导出文件缺失：{f.name}（subtitle 阶段未产出）")
        if f.stat().st_size == 0:
            warnings.append(f"{f.name} 为空：本集没有已翻译的行（请在工作台编辑译文后重跑 subtitle/export）")
            continue
        files.append({"name": f.name, "size": f.stat().st_size, "url_path":
                      f"/api/v1/episodes/{job.episode_id}/exports/{f.name}"})
    if not files:
        raise PipelineError("没有任何可下载的导出文件（全部为空）：" + "；".join(warnings))
    return {"files": files, "warnings": warnings}


STAGE_FUNCS: dict[str, Callable[..., Awaitable[dict]]] = {
    "ingest": stage_ingest,
    "asr": stage_asr,
    "translate": stage_translate,
    "subtitle": stage_subtitle,
    "export": stage_export,
}


# ---------------- 状态机 ----------------

def _now():
    return datetime.now(timezone.utc)


async def _set_stage(session: AsyncSession, sr: StageRun, state: str, error: str | None = None) -> None:
    sr.state = state
    if error is not None:
        sr.error = error
    elif state == "running":
        sr.error = ""  # 新一次尝试开始，清掉上一轮残留的报错/备注
    if state == "running":
        sr.started_at = _now()
    if state in ("succeeded", "failed", "skipped"):
        sr.finished_at = _now()
    await session.commit()


async def _fail_job(session: AsyncSession, job: PipelineJob, stage: str, error: str) -> None:
    job.state = "failed"
    job.current_stage = stage
    job.error = error
    job.finished_at = _now()
    await session.commit()
    logger.error(f"Job {job.id} 在 {stage} 失败：{error}")


async def _finish_job(session: AsyncSession, job: PipelineJob) -> None:
    job.state = "succeeded"
    job.error = ""
    job.finished_at = _now()
    await session.commit()
    logger.success(f"Job {job.id} 完成")


async def _get_stage_run(session: AsyncSession, job: PipelineJob, stage: str) -> StageRun:
    """取或创建 StageRun（任务创建时未预生成的 Stage 在此懒创建）。"""
    res = await session.execute(
        select(StageRun).where(StageRun.job_id == job.id, StageRun.stage == stage)
    )
    sr = res.scalars().first()
    if sr is None:
        sr = StageRun(job_id=job.id, stage=stage, seq=STAGE_ORDER.index(stage))
        session.add(sr)
        await session.flush()
    return sr


async def _fresh_job(session: AsyncSession, job_id: str) -> PipelineJob:
    """rollback 会让已加载实体过期（属性访问触发同步 IO → MissingGreenlet），
    因此循环内每次都重新加载。"""
    return await session.get(PipelineJob, job_id)


async def run_stages(job_id: str, from_stage: str | None = None) -> None:
    """执行一个 Job：从 from_stage（默认全部）开始顺序跑完。

    断点续跑：from_stage 之前的 succeeded Stage 不再执行，产物直接复用。
    本函数自管理 DB 会话（在后台任务中运行，不复用请求级会话）。
    """
    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(PipelineJob, job_id)
        if not job:
            logger.error(f"Job {job_id} 不存在")
            return
        if job.state == "canceled":
            job_control.clear(job_id)
            return
        job.state = "running"
        await session.commit()

        run_list = [s for s in ((job.config or {}).get("stages") or STAGE_ORDER) if s in STAGE_ORDER]
        if not run_list:
            await _fail_job(session, await _fresh_job(session, job_id), "", "config.stages 无有效值")
            job_control.clear(job_id)
            return

        if from_stage:
            if from_stage not in STAGE_ORDER:
                await _fail_job(session, await _fresh_job(session, job_id), from_stage, f"未知 Stage：{from_stage}")
                job_control.clear(job_id)
                return
            run_list = STAGE_ORDER[STAGE_ORDER.index(from_stage):]
            # 重置 from_stage 起的 Stage 状态与产物
            for st in run_list:
                sr = await _get_stage_run(session, job, st)
                await _set_stage(session, sr, "pending")
            clear_artifacts_from(job_id, from_stage)
        else:
            for st in run_list:
                await _get_stage_run(session, job, st)

        ctx = {"canceled": job_control.is_canceled}
        try:
            for stage in run_list:
                if job_control.is_canceled(job_id):
                    job = await _fresh_job(session, job_id)
                    job.state = "canceled"
                    job.finished_at = _now()
                    await session.commit()
                    logger.info(f"Job {job_id} 已取消")
                    return
                job = await _fresh_job(session, job_id)
                sr = await _get_stage_run(session, job, stage)
                if sr.state == "succeeded" and load_artifact(job_id, stage) is not None:
                    logger.info(f"Job {job_id} Stage[{stage}] 已有产物，跳过")
                    continue
                job.current_stage = stage
                await session.commit()
                # 单 Stage 自动重试（指数退避在 LLM 层已有；这里针对基础设施型失败）
                attempt = 0
                while True:
                    attempt += 1
                    job = await _fresh_job(session, job_id)
                    sr = await _get_stage_run(session, job, stage)
                    sr.retry_count = attempt - 1
                    await _set_stage(session, sr, "running")
                    try:
                        payload = await STAGE_FUNCS[stage](session, job, sr, ctx)
                        sr.artifact_path = save_artifact(job.id, stage, payload)
                        await _set_stage(session, sr, "succeeded")
                        break
                    except Exception as e:  # noqa: BLE001
                        await session.rollback()
                        # 重取实体再记失败（rollback 使对象过期）
                        job = await _fresh_job(session, job_id)
                        sr = await _get_stage_run(session, job, stage)
                        await _set_stage(session, sr, "failed", str(e)[:2000])
                        if attempt >= MAX_STAGE_AUTO_RETRY:
                            await _fail_job(session, job, stage,
                                            f"Stage[{stage}] 连续 {attempt} 次失败：{e}")
                            return
                        wait = min(30.0, 2.0 ** attempt)
                        logger.warning(f"Stage[{stage}] 第 {attempt} 次失败：{e}；{wait}s 后重试")
                        await asyncio.sleep(wait)
            await _finish_job(session, await _fresh_job(session, job_id))
        finally:
            job_control.clear(job_id)


async def retry_stage(job_id: str, stage: str) -> None:
    """单 Stage 重试：重置该 Stage 及其下游，从该 Stage 续跑（产物复用上游）。"""
    if stage not in STAGE_ORDER:
        raise PipelineError(f"未知 Stage：{stage}")
    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(PipelineJob, job_id)
        if not job:
            raise PipelineError("任务不存在")
        if job.state == "running":
            raise PipelineError("任务正在运行中，无法重试")
        job.state = "queued"
        job.error = ""
        await session.commit()
    asyncio.create_task(_run_with_error_handling(job_id, stage))


async def _run_with_error_handling(job_id: str, stage: str | None) -> None:
    try:
        await run_stages(job_id, from_stage=stage)
    except Exception:  # noqa: BLE001
        logger.exception(f"Job {job_id} 后台执行异常")


def enqueue_job(job_id: str, from_stage: str | None = None) -> None:
    """创建任务后调用：后台启动流水线。"""
    asyncio.create_task(_run_with_error_handling(job_id, from_stage))
