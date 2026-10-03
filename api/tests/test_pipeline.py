"""流水线状态机测试（F-M1-07）与 SRT 导出测试（F-M1-06）。

不依赖真实 faster-whisper / LLM / ffmpeg：
- ASR：MockASRProvider 读产物 audio.wav 同目录的 audio.srt
- LLM：FakeLLMProvider（tests.test_translate）
- ffmpeg 探测/抽轨：FakeMedia 打桩
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("pysubs2")

from app.services.pipeline import engine as pe


def make_srt(texts: list[str]) -> str:
    """MockASR 用 SRT：每条 4s 间隔、持续 3s。"""
    events = []
    for i, t in enumerate(texts):
        s, e = i * 4, i * 4 + 3
        events.append(
            f"{i + 1}\n00:{s // 60:02d}:{s % 60:02d},000 --> "
            f"00:{e // 60:02d}:{e % 60:02d},000\n{t}\n"
        )
    return "\n".join(events)


def seed_mock_srt(episode_id_or_env, texts):
    """MockASR 约定：audio.wav 同名 .srt 提供转录结果。

    episode_id_or_env: episode id 字符串，或含 "episode_id" 键的 dict。
    """
    from app.config import get_settings

    eid = episode_id_or_env["episode_id"] if isinstance(episode_id_or_env, dict) else episode_id_or_env
    s = get_settings()
    p = s.data_dir / "media" / eid / "audio.srt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(make_srt(texts), encoding="utf-8")
    return p


async def seed_video(pipeline_env):
    """造一个假视频文件并把路径写回 episode（模拟 upload_video 的结果）。"""
    from app.config import get_settings
    from app.db.session import get_session_factory
    from app.models.entities import Episode

    s = get_settings()
    rel = Path("media") / pipeline_env["episode_id"] / "video.mp4"
    p = s.data_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake-video-bytes")

    async with get_session_factory()() as session:
        ep = await session.get(Episode, pipeline_env["episode_id"])
        ep.video_path = rel.as_posix()
        ep.video_name = "video.mp4"
        await session.commit()
    return p


async def _create_job(pipeline_env, stages=None) -> str:
    from app.db.session import get_session_factory
    from app.models.entities import PipelineJob

    async with get_session_factory()() as session:
        job = PipelineJob(episode_id=pipeline_env["episode_id"], config={"stages": stages or []})
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _load_job(job_id):
    from app.db.session import get_session_factory
    from app.models.entities import PipelineJob, StageRun

    async with get_session_factory()() as session:
        job = await session.get(PipelineJob, job_id)
        stages = (await session.execute(
            select(StageRun).where(StageRun.job_id == job_id).order_by(StageRun.seq)
        )).scalars().all()
        return job, stages


@pytest.mark.asyncio
async def test_full_pipeline_succeeds(pipeline_env):
    """验收 F-M1-07：五 Stage 顺序成功；F-M1-01：元信息回写；F-M1-06：SRT 落盘。"""
    await seed_video(pipeline_env)
    seed_mock_srt(pipeline_env, ["おはよう", "今日もいい天気だね", "そうだね"])

    job_id = await _create_job(pipeline_env)
    await pe.run_stages(job_id)

    job, stages = await _load_job(job_id)
    assert job.state == "succeeded", f"job.error={job.error}; stages={[(s.stage, s.state, s.error) for s in stages]}"
    assert [s.stage for s in stages] == ["ingest", "asr", "translate", "subtitle", "export"]
    assert all(s.state == "succeeded" for s in stages)

    # ingest 产物与剧集元信息
    ingest = pe.load_artifact(job_id, "ingest")
    assert ingest["duration_ms"] == 12_000 and ingest["width"] == 1920

    # segment 落库且译文齐全
    from app.db.session import get_session_factory
    from app.models.entities import Episode, Segment

    async with get_session_factory()() as session:
        ep = await session.get(Episode, pipeline_env["episode_id"])
        assert ep.duration_ms == 12_000 and ep.status == "ready"
        rows = (await session.execute(
            select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.index)
        )).scalars().all()
        assert len(rows) == 3
        assert all(r.target_text for r in rows)
        assert all(r.status == "translated" for r in rows)

    # SRT 产物
    from app.config import get_settings

    s = get_settings()
    zh = s.data_dir / "exports" / pipeline_env["episode_id"] / "E01.zh.srt"
    bi = s.data_dir / "exports" / pipeline_env["episode_id"] / "E01.bilingual.srt"
    assert zh.exists() and bi.exists()
    content = bi.read_text(encoding="utf-8")
    assert "おはよう" in content and "【おはよう】" in content


@pytest.mark.asyncio
async def test_already_succeeded_stage_not_rerun(pipeline_env):
    """验收 F-M1-07：重跑已成功 Stage 不重复执行（断点续跑）。"""
    await seed_video(pipeline_env)
    seed_mock_srt(pipeline_env, ["第一句", "第二句"])
    job_id = await _create_job(pipeline_env)
    await pe.run_stages(job_id)
    assert pipeline_env["fake_media"].extract_calls == 1

    # 再次整任务重跑：ingest 产物已存在 → 抽轨不会执行第二次
    await pe.run_stages(job_id)
    assert pipeline_env["fake_media"].extract_calls == 1


@pytest.mark.asyncio
async def test_failed_stage_retried_then_recovers(pipeline_env):
    """验收 F-M1-07：失败 Stage 自动重试后成功；重试次数可查。"""
    await seed_video(pipeline_env)
    seed_mock_srt(pipeline_env, ["あ", "い"])
    pipeline_env["fake_media"].fail_next = 2  # 抽轨失败 2 次 → 第 3 次成功
    job_id = await _create_job(pipeline_env)
    await pe.run_stages(job_id)

    job, stages = await _load_job(job_id)
    assert job.state == "succeeded"
    ingest = next(s for s in stages if s.stage == "ingest")
    assert ingest.state == "succeeded"
    assert ingest.retry_count == 2


@pytest.mark.asyncio
async def test_stage_subset_rerun_translate_only(pipeline_env):
    """断点续跑：只重跑 translate（指定 Stage 子集）不重跑上游。"""
    await seed_video(pipeline_env)
    seed_mock_srt(pipeline_env, ["りんご", "みかん"])
    job_id = await _create_job(pipeline_env)
    await pe.run_stages(job_id)
    extract_calls = pipeline_env["fake_media"].extract_calls

    # 从 translate 重跑：上游产物复用
    await pe.run_stages(job_id, from_stage="translate")
    job, stages = await _load_job(job_id)
    assert job.state == "succeeded"
    assert pipeline_env["fake_media"].extract_calls == extract_calls  # 上游未重跑


@pytest.mark.asyncio
async def test_export_with_zero_translations_succeeds(pipeline_env):
    """回归：全部行翻译失败时，单语 SRT 为 0 字节不应导致 export 阶段失败。

    真实案例：1 行台词翻译失败 → E26.zh.srt 0 字节 → export 报"缺失或为空"。
    现策略：空单语降级为 warning（双语文件仍含原文），只有文件缺失才失败。
    """
    await seed_video(pipeline_env)
    seed_mock_srt(pipeline_env, ["お腹すいたね。"])

    # 打桩翻译：全部行失败（模拟渠道返回解析不出的情况）
    from app.services import provider_factory

    async def _build_llm_fail(session, job):
        from tests.test_translate import FakeLLMProvider

        return FakeLLMProvider(drop_pattern={1}, always_drop=True)

    import app.services.provider_factory as pf
    pf.build_llm_provider = _build_llm_fail

    from app.config import get_settings
    from app.db.session import get_session_factory
    from app.models.entities import PipelineJob

    async with get_session_factory()() as session:
        job = PipelineJob(episode_id=pipeline_env["episode_id"], config={})
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    await pe.run_stages(job_id)

    job, stages = await _load_job(job_id)
    assert job.state == "succeeded", f"error={job.error}"
    export = next(s for s in stages if s.stage == "export")
    assert export.state == "succeeded"

    s = get_settings()
    bi = s.data_dir / "exports" / pipeline_env["episode_id"] / "E01.bilingual.srt"
    zh = s.data_dir / "exports" / pipeline_env["episode_id"] / "E01.zh.srt"
    assert bi.exists() and bi.stat().st_size > 0   # 双语含原文
    assert zh.exists() and zh.stat().st_size == 0  # 单语为空但不阻塞


@pytest.mark.asyncio
async def test_missing_video_fails_with_clear_error(pipeline_env):
    """未上传视频 → 任务失败且报错明确。"""
    job_id = await _create_job(pipeline_env)
    await pe.run_stages(job_id)
    job, stages = await _load_job(job_id)
    assert job.state == "failed"
    ingest = next(s for s in stages if s.stage == "ingest")
    assert "尚未上传视频" in ingest.error


@pytest.mark.asyncio
async def test_mock_asr_provider_reads_srt(pipeline_env):
    """MockASRProvider 行为：读同名 .srt 并转换为 TranscriptLine。"""
    from app.providers.asr import MockASRProvider

    await seed_video(pipeline_env)
    seed_mock_srt(pipeline_env, ["テスト", "確認"])
    from app.config import get_settings

    wav = get_settings().data_dir / "media" / pipeline_env["episode_id"] / "audio.wav"
    lines = MockASRProvider().transcribe(str(wav), language="ja")
    assert [l.text for l in lines] == ["テスト", "確認"]
    assert lines[0].start_ms == 0 and lines[0].end_ms == 3000


def test_srt_export_formats(temp_env):
    """验收 F-M1-06：单语与双语 SRT 逐行对应。"""
    from app.services.srt_export import export_srt_bilingual, export_srt_target

    segs = [
        {"index": 1, "start_ms": 0, "end_ms": 2000, "source_text": "こんにちは", "target_text": "你好"},
        {"index": 2, "start_ms": 2500, "end_ms": 5000, "source_text": "ありがとう", "target_text": "谢谢"},
    ]
    out1 = temp_env.exports_dir / "single.srt"
    out2 = temp_env.exports_dir / "bi.srt"
    export_srt_target(segs, str(out1))
    export_srt_bilingual(segs, str(out2))

    single = out1.read_text(encoding="utf-8")
    assert "你好" in single and "こんにちは" not in single

    bi = out2.read_text(encoding="utf-8")
    assert "こんにちは" in bi and "你好" in bi
    # 双语行顺序：日上中下
    assert bi.index("こんにちは") < bi.index("你好")

    # 空译文行：单语跳过、双语保留原文
    segs.append({"index": 3, "start_ms": 6000, "end_ms": 8000, "source_text": "未訳", "target_text": ""})
    export_srt_target(segs, str(out1))
    assert "未訳" not in out1.read_text(encoding="utf-8")
