"""流水线状态机与翻译容错测试（F-M1-04/05/07 核心路径）。

不依赖真实 faster-whisper / LLM：
- ASR 用 MockASRProvider（读同目录 .srt）
- LLM 用 FakeLLMProvider（确定性返回，含缺行场景）
- ffmpeg 探测/抽轨用 monkeypatch 假实现（真 ffmpeg 的集成测试另跑）
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")


# ---------- LLM Provider 层 ----------

class TestJsonForgiving:
    def test_plain(self):
        from app.providers.llm import parse_json_forgiving

        assert parse_json_forgiving('[{"i":1,"zh":"你好"}]') == [{"i": 1, "zh": "你好"}]

    def test_fenced(self):
        from app.providers.llm import parse_json_forgiving

        text = '```json\n[{"i": 1, "zh": "早上好"}]\n```'
        assert parse_json_forgiving(text) == [{"i": 1, "zh": "早上好"}]

    def test_with_prefix_noise(self):
        from app.providers.llm import parse_json_forgiving

        text = '好的，以下是译文：\n[{"i":1,"zh":"早上"},{"i":2,"zh":"午安"}] 希望有帮助'
        assert len(parse_json_forgiving(text)) == 2

    def test_trailing_comma(self):
        from app.providers.llm import parse_json_forgiving

        assert parse_json_forgiving('[{"i":1,"zh":"a"},]') == [{"i": 1, "zh": "a"}]

    def test_garbage_raises(self):
        from app.providers.llm import LLMError, parse_json_forgiving

        with pytest.raises(LLMError):
            parse_json_forgiving("完全不是 JSON 的回复")


class FakeLLMProvider:
    """确定性假渠道：可配置每批丢几行，验证补齐重试逻辑。

    always_drop=True 时永远返回不含行内容的响应（模拟整批失败）。
    """

    def __init__(self, drop_pattern: set[int] | None = None, garbage_once: bool = False,
                 always_drop: bool = False):
        self.channel = type("C", (), {"price_in_per_m": 1.0, "price_out_per_m": 2.0,
                                      "name": "fake"})()
        self.drop_pattern = drop_pattern or set()
        self.garbage_once = garbage_once
        self.always_drop = always_drop
        self.calls = 0
        self.aclosed = False

    async def chat(self, messages, json_mode=True, max_retries=3, max_tokens=4096):
        self.calls += 1
        import re

        batch = []
        for line in messages[-1]["content"].splitlines():
            m = re.match(r'\{"i": (\d+), "src": "(.*)"\}', line)
            if m:
                batch.append((int(m.group(1)), m.group(2)))
        if self.garbage_once and self.calls == 1:
            self.garbage_once = False
            return type("R", (), {"content": "我不是 JSON",
                                  "prompt_tokens": 10, "completion_tokens": 5})()
        items = []
        for i, src in batch:
            global_i = i
            if global_i in self.drop_pattern and self.calls < 2:
                continue  # 第一轮故意缺行
            items.append({"i": i, "zh": f"【{src}】"})
        if self.always_drop:
            items = []
        return type("R", (), {
            "content": json.dumps(items, ensure_ascii=False),
            "prompt_tokens": 100, "completion_tokens": 50,
        })()

    async def aclose(self):
        self.aclosed = True


class TestTranslateEngine:
    @pytest.mark.asyncio
    async def test_single_line_object_response(self):
        """回归：模型对单行批次返回 JSON 对象 {i,zh}（而非数组）也必须解析成功。

        真实案例：glm-5.3-flash 对单行批次稳定返回对象形式，导致整集翻译"缺行"。
        """
        from app.services.translate_engine import TranslateEngine

        class ObjectStyleLLM:
            def __init__(self):
                self.channel = type("C", (), {"price_in_per_m": 0, "price_out_per_m": 0, "name": "x"})()

            async def chat(self, messages, json_mode=True, max_retries=3, max_tokens=4096):
                import json as _json

                return type("R", (), {
                    "content": _json.dumps({"i": 1, "zh": "肚子饿了呢。"}, ensure_ascii=False),
                    "prompt_tokens": 10, "completion_tokens": 5,
                })()

            async def aclose(self):
                pass

        engine = TranslateEngine(ObjectStyleLLM(), batch_size=15)
        res = await engine.translate_lines(
            [{"index": 1, "source_text": "お腹すいたね。"}], terms=[])
        assert res.translations == {1: "肚子饿了呢。"}
        assert res.failed_indices == []

    @pytest.mark.asyncio
    async def test_output_lines_equal_input(self):
        """验收 F-M1-04：输出行数 = 输入行数。"""
        from app.services.translate_engine import TranslateEngine

        lines = [{"index": i, "source_text": f"セリフ{i}"} for i in range(1, 41)]
        engine = TranslateEngine(FakeLLMProvider(), batch_size=15)
        res = await engine.translate_lines(lines, terms=[])
        assert len(res.translations) == 40
        assert res.failed_indices == []
        assert res.prompt_tokens > 0

    @pytest.mark.asyncio
    async def test_missing_lines_retried(self):
        """缺行 → 补齐重试后全部完成。"""
        from app.services.translate_engine import TranslateEngine

        lines = [{"index": i, "source_text": f"テスト{i}"} for i in range(1, 21)]
        engine = TranslateEngine(FakeLLMProvider(drop_pattern={3, 17}), batch_size=15)
        res = await engine.translate_lines(lines, terms=[])
        assert 3 in res.translations and 17 in res.translations
        assert res.failed_indices == []

    @pytest.mark.asyncio
    async def test_garbage_response_recovered(self):
        """JSON 解析失败 → 批级重试恢复。"""
        from app.services.translate_engine import TranslateEngine

        lines = [{"index": 1, "source_text": "こんにちは"}, {"index": 2, "source_text": "さようなら"}]
        engine = TranslateEngine(FakeLLMProvider(garbage_once=True), batch_size=15)
        res = await engine.translate_lines(lines, terms=[])
        assert res.translations[1] == "【こんにちは】"


class TestTermInjection:
    @pytest.mark.asyncio
    async def test_glossary_hit_injected_into_prompt(self):
        """验收 F-M1-05：命中术语表注入 prompt。"""
        from app.services.translate_engine import _match_terms, build_batch_messages

        terms = [{"source": "フリーレン", "target": "芙莉莲", "aliases": ["芙莉"], "locked": True}]
        assert _match_terms("フリーレンは魔法使いです", terms)[0]["target"] == "芙莉莲"
        assert _match_terms("芙莉ちゃん", terms)[0]["target"] == "芙莉莲"  # 别名命中
        assert _match_terms("関係ないセリフ", terms) == []

        messages = build_batch_messages(
            batch_start=1,
            batch_items=[(1, "フリーレンは魔法使いです")],
            prev_translated=[], next_source=[], terms=terms,
        )
        assert "フリーレン → 芙莉莲" in messages[1]["content"]
        assert "资深动漫字幕翻译" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_context_window_present(self):
        from app.services.translate_engine import build_batch_messages

        messages = build_batch_messages(
            batch_start=16,
            batch_items=[(16, "行十六")],
            prev_translated=["行十五 → 十五", "行十四 → 十四"],
            next_source=["行十九", "行二十"],
            terms=[],
        )
        user = messages[1]["content"]
        assert "行十五 → 十五" in user  # 前文已译
        assert "行十九" in user  # 后文原文
        assert "i 从 1 到 1" in user
