"""HTTP API 端到端测试（F-M1-08 验收：上传→任务→字幕→下载 全流程无需命令行）。

走真实 FastAPI 路由（ASGITransport），仅打桩 ASR/LLM/ffmpeg 探测。
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("pysubs2")

from tests.test_pipeline import seed_mock_srt, seed_video  # noqa: E402


async def _poll_job(client: AsyncClient, job_id: str, timeout_s: float = 15.0) -> dict:
    for _ in range(int(timeout_s / 0.1)):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/v1/jobs/{job_id}")
        job = r.json()
        if job["state"] in ("succeeded", "failed", "canceled"):
            return job
    raise AssertionError(f"任务 {timeout_s}s 内未结束: {job}")


@pytest.mark.asyncio
async def test_api_full_flow(pipeline_env):
    """创建项目 → 建集 → 上传视频 → 启动任务 → 轮询成功 → 字幕行 → 下载 SRT。"""
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)  # 不跑 lifespan（db_env 已建表）
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. 健康检查
        r = await client.get("/api/v1/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"

        # 2. 项目
        r = await client.post("/api/v1/projects", json={"title": "E2E 番剧"})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        r = await client.get("/api/v1/projects")
        assert any(p["title"] == "E2E 番剧" for p in r.json())

        # 3. 剧集
        r = await client.post(f"/api/v1/episodes?project_id={pid}",
                              json={"number": 1, "title": "第一集"})
        assert r.status_code == 201, r.text
        eid = r.json()["id"]

        # 4. 上传视频（FakeMedia.probe 返回 1920x1080 / 12s）
        r = await client.post(
            f"/api/v1/episodes/{eid}/video",
            files={"file": ("video.mp4", b"fake-video-bytes", "video/mp4")},
        )
        assert r.status_code == 200, r.text
        ep = r.json()
        assert ep["width"] == 1920 and ep["duration_ms"] == 12_000 and ep["status"] == "ready"

        # 5. 启动流水线（MockASR 读 audio.srt，FakeLLM 逐行翻译）
        seed_mock_srt(eid, ["おはよう", "今日もいい天気だね", "そうだね"])
        r = await client.post(f"/api/v1/episodes/{eid}/jobs", json={})
        assert r.status_code == 201, r.text
        job = await _poll_job(client, r.json()["id"])
        assert job["state"] == "succeeded", f"error={job['error']}"
        assert [s["stage"] for s in job["stages"]] == ["ingest", "asr", "translate", "subtitle", "export"]
        assert job["token_cost_json"]["prompt_tokens"] > 0

        # 6. 字幕行可预览/编辑
        r = await client.get(f"/api/v1/episodes/{eid}/segments")
        segs = r.json()
        assert len(segs) == 3
        assert all(s["target_text"].startswith("【") for s in segs)
        r = await client.put(f"/api/v1/episodes/{eid}/segments/{segs[0]['id']}",
                             json={"target_text": "早上好"})
        assert r.status_code == 200 and r.json()["target_text"] == "早上好"
        assert r.json()["status"] == "reviewed"  # 人工编辑后视为已复核

        # 7. 导出列表 + 下载（字母序：bilingual 在前）
        r = await client.get(f"/api/v1/episodes/{eid}/exports")
        files = r.json()
        assert {f["name"] for f in files} == {"E01.zh.srt", "E01.bilingual.srt"}
        by_name = {f["name"]: f for f in files}
        r = await client.get(by_name["E01.bilingual.srt"]["url"])
        assert r.status_code == 200
        # 双语：日上中下（FakeLLM 的"译文"为【原文】占位）
        assert "おはよう" in r.text and "【おはよう】" in r.text
        r = await client.get(by_name["E01.zh.srt"]["url"])
        assert r.status_code == 200
        assert "おはよう" not in r.text.replace("【おはよう】", "")  # 单语不含裸原文

        # 8. 断点续跑：只重跑 translate 不重跑上游（抽轨不执行第二次）
        extract_before = pipeline_env["fake_media"].extract_calls
        r = await client.post(f"/api/v1/jobs/{job['id']}/retry", json={"stage": "translate"})
        assert r.status_code == 200
        job2 = await _poll_job(client, job["id"])
        assert job2["state"] == "succeeded"
        assert pipeline_env["fake_media"].extract_calls == extract_before


@pytest.mark.asyncio
async def test_channel_connectivity(pipeline_env):
    """F-M1-04 回归：/test 接口成功路径与失败路径（永不 500）。"""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.main import create_app

    # 本地 mock OpenAI 兼容服务
    class MockHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            body = _json.dumps({
                "choices": [{"message": {"content": '{"zh": "早上好"}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
                "model": "mock-model",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: ANN002
            pass

    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
        # 成功路径：指向本地 mock
        r = await client.post("/api/v1/llm/channels", json={
            "name": "mock", "base_url": f"http://127.0.0.1:{port}",
            "api_key": "sk-mock", "model": "mock-model"})
        ch_ok = r.json()
        r = await client.post(f"/api/v1/llm/channels/{ch_ok['id']}/test")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True, body
        assert "早上好" in body["reply"]

        # 失败路径：不可达地址 → ok=False + 可读原因（而非 500）
        r = await client.post("/api/v1/llm/channels", json={
            "name": "dead", "base_url": "http://127.0.0.1:9",
            "api_key": "sk-x", "model": "x"})
        ch_dead = r.json()
        r = await client.post(f"/api/v1/llm/channels/{ch_dead['id']}/test")
        assert r.status_code == 200, f"test 接口不应 500: {r.status_code} {r.text}"
        body = r.json()
        assert body["ok"] is False and body["message"]

        await client.delete(f"/api/v1/llm/channels/{ch_ok['id']}")
        await client.delete(f"/api/v1/llm/channels/{ch_dead['id']}")
    server.shutdown()


@pytest.mark.asyncio
async def test_glossary_and_channel_api(pipeline_env):
    """术语表 CRUD（F-M1-05）+ 渠道 CRUD（F-M1-04）。"""
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 术语：全局 + 项目级
        r = await client.post("/api/v1/glossary",
                              json={"source": "フリーレン", "target": "芙莉莲", "aliases": ["芙莉"],
                                    "locked": True})
        assert r.status_code == 201
        term_global = r.json()
        assert term_global["project_id"] is None

        r = await client.post("/api/v1/projects", json={"title": "术语项目"})
        pid = r.json()["id"]
        r = await client.post("/api/v1/glossary",
                              json={"source": "ゼーリエ", "target": "费伦", "project_id": pid})
        assert r.status_code == 201 and r.json()["project_id"] == pid

        # 按 project 过滤：全局 + 项目词条都应出现
        r = await client.get("/api/v1/glossary", params={"project_id": pid})
        assert len(r.json()) == 2

        r = await client.put(f"/api/v1/glossary/{term_global['id']}",
                             json={"note": "主角", "locked": False})
        assert r.json()["note"] == "主角" and r.json()["locked"] is False
        r = await client.delete(f"/api/v1/glossary/{term_global['id']}")
        assert r.status_code == 204

        # 渠道：api_key 加密落库 + 返回打码
        r = await client.post("/api/v1/llm/channels",
                              json={"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                                    "api_key": "sk-test-1234567890abcdef", "model": "deepseek-chat"})
        assert r.status_code == 201
        ch = r.json()
        assert ch["api_key_masked"].startswith("sk-t") and "1234567890" not in ch["api_key_masked"]
        assert "sk-test-1234567890abcdef" not in str(ch)

        r = await client.put(f"/api/v1/llm/channels/{ch['id']}", json={"temperature": 0.7})
        assert r.json()["temperature"] == 0.7
        # 不传 api_key → 保留原密文
        r = await client.put(f"/api/v1/llm/channels/{ch['id']}", json={"api_key": ""})
        assert r.json()["api_key_masked"].count("*") > 0
        r = await client.delete(f"/api/v1/llm/channels/{ch['id']}")
        assert r.status_code == 204
