"""pytest 共享夹具：隔离的临时数据目录 / DB / 配置（env 变量注入）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 确保 app 包可导入（api/ 目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    """每个测试独立 data 目录与 DB；get_settings 缓存按用例清理重建。"""
    from cryptography.fernet import Fernet

    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(data_dir / 'test.db').as_posix()}")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "runtime_config.json"))

    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()  # 建目录 + 秘钥就绪
    yield s
    get_settings.cache_clear()


@pytest.fixture()
async def db_env(temp_env):
    """在测试的事件循环内建表/收尾。"""
    from app.db import session as db_session

    db_session._engine = None
    db_session._session_factory = None
    await db_session.init_db()
    yield temp_env
    await db_session.dispose_engine()


@pytest.fixture()
async def pipeline_env(db_env, monkeypatch):
    """种子数据：项目/剧集，打桩 media（探测/抽轨）与 LLM 构建 —— 跑纯状态机。"""
    from app.db.session import get_session_factory
    from app.models.entities import Episode, Project
    from app.services import provider_factory

    class FakeMedia:
        def __init__(self):
            self.extract_calls = 0
            self.fail_next = 0  # >0 时接下来 N 次 extract_audio 抛错

        async def probe(self, video_path):
            from app.services.media import MediaError, MediaInfo

            if not video_path or not Path(video_path).exists():
                raise MediaError("视频文件不存在")
            return MediaInfo(duration_ms=12_000, width=1920, height=1080,
                             audio_tracks=[{"index": 1, "codec": "aac", "language": "jpn", "channels": 2}])

        async def extract_audio(self, video_path, out_wav):
            from app.services.media import MediaError

            if self.fail_next > 0:
                self.fail_next -= 1
                raise MediaError("模拟抽轨失败")
            Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
            Path(out_wav).write_bytes(b"RIFF-fake-wav-data")
            self.extract_calls += 1
            return out_wav

    async def _seed():
        async with get_session_factory()() as s:
            p = Project(title="测试番剧")
            s.add(p)
            await s.flush()
            e = Episode(project_id=p.id, number=1, title="第一集")
            s.add(e)
            await s.commit()
            return p.id, e.id

    project_id, episode_id = await _seed()

    fake_media = FakeMedia()
    # 在源头模块打桩：engine / episodes 路由都通过 app.services.media 调用
    monkeypatch.setattr("app.services.media.probe", fake_media.probe)
    monkeypatch.setattr("app.services.media.extract_audio", fake_media.extract_audio)

    async def _build_llm(session, job):
        from tests.test_translate import FakeLLMProvider

        return FakeLLMProvider()

    monkeypatch.setattr(provider_factory, "build_llm_provider", _build_llm)

    def _build_asr(model_size: str = "small"):
        from app.providers.asr import MockASRProvider

        return MockASRProvider()

    monkeypatch.setattr(provider_factory, "build_asr_provider", _build_asr)

    return {"project_id": project_id, "episode_id": episode_id, "fake_media": fake_media}
