"""系统设置 API 测试：路径可配（GET/PUT）、校验与持久化。"""
from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")


@pytest.fixture()
def settings_client(temp_env):
    from app.main import create_app

    return create_app()


@pytest.mark.asyncio
async def test_get_settings_returns_effective_paths(settings_client):
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings

    s = get_settings()
    transport = ASGITransport(app=settings_client)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/settings")
        assert r.status_code == 200
        data = r.json()
        assert data["data_dir"] == str(s.data_dir)
        assert data["models_dir"] == str(s.resolved_models_dir)
        assert set(data["derived"].keys()) == {"媒体目录 media", "任务产物 artifacts", "导出成品 exports"}
        assert all(v["exists"] for v in data["derived"].values())  # 启动时已创建
        assert "data_dir" in data["restart_required_fields"]


@pytest.mark.asyncio
async def test_put_settings_persists_and_applies(settings_client, tmp_path):
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings, runtime_config_path

    new_models = tmp_path / "whisper-models"
    transport = ASGITransport(app=settings_client)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.put("/api/v1/settings", json={
            "models_dir": str(new_models),
            "asr_beam_size": 1,
            "ffmpeg_path": "",  # 留空 = 回落 PATH 自动
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["models_dir"] == str(new_models)
        assert data["asr_beam_size"] == 1

        # 持久化到 runtime_config.json
        cfg = json.loads(runtime_config_path().read_text(encoding="utf-8"))
        assert cfg["models_dir"] == str(new_models)
        assert cfg["asr_beam_size"] == 1
        assert "ffmpeg_path" not in cfg  # 空值 = 清除，回落自动

        # 新进程语义：cache 清空后 get_settings 读到新值
        get_settings.cache_clear()
        assert str(get_settings().resolved_models_dir) == str(new_models)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_put_settings_validations(settings_client, tmp_path):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=settings_client)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 不存在的 ffmpeg 路径 → 422
        r = await client.put("/api/v1/settings", json={"ffmpeg_path": "X:/nope/ffmpeg.exe"})
        assert r.status_code == 422
        assert "不存在" in r.json()["detail"]

        # 非法 asr_device → 422
        r = await client.put("/api/v1/settings", json={"asr_device": "tpu"})
        assert r.status_code == 422

        # 非 SQLite 数据库 → 422
        r = await client.put("/api/v1/settings", json={"database_url": "postgresql://x"})
        assert r.status_code == 422

        # data_dir 指向文件 → 422（无法创建目录）
        occupied = tmp_path / "occupied"
        occupied.write_text("x")
        r = await client.put("/api/v1/settings", json={"data_dir": str(occupied)})
        assert r.status_code == 422
