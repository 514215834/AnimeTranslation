"""翻译引擎（F-M1-04/05 / 架构文档 4.2）。

- 分批翻译：每批 N 行，携带前 3 句已译 + 后 3 句原文窗口上下文
- 术语表命中注入 prompt（含别名），锁定词条译法
- JSON 结构化输出 [{"i": 行号, "zh": 译文}]，行号校验，缺行单独补齐重试
- 指数退避重试；多轮兜底累计缺行
- token 用量累计返回
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from loguru import logger

from app.providers.llm import LLMError, LLMProvider, estimate_cost, parse_json_forgiving

SYSTEM_PROMPT = (
    "你是一名资深动漫字幕翻译，负责将{source_lang_name}字幕翻译为简体中文。\n"
    "要求：\n"
    "1. 译文口语化、自然，符合动漫台词语气，保留语气词处理（如 えっ→诶？）；\n"
    "2. 人名、地名、招式名、组织名严格按【术语表】的译法，不得自创；\n"
    "3. 称谓一致（様→大人/阁下按语境统一，ちゃん→小~）；\n"
    "4. 不添加解释、注释或原文中不存在的内容；\n"
    "5. 每条译文尽量简短，适合单行字幕展示（不超过 18 个汉字为宜）；\n"
    "6. 输出 JSON 数组：每条 [{{\"i\": 行号(int), \"zh\": \"译文\"}}]，行号与输入一一对应，不得增减行。"
)

TERMS_HEADER = "【术语表】（必须严格遵守的译名映射）"
CONTEXT_HEADER = "【上下文提示】"


@dataclass
class TranslateResult:
    translations: dict[int, str] = field(default_factory=dict)  # 行号 -> 译文
    failed_indices: list[int] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    def merge(self, other: "TranslateResult") -> None:
        self.translations.update(other.translations)
        self.failed_indices = other.failed_indices  # 合并语义：最后一轮的缺行
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost += other.cost


def _match_terms(line_text: str, terms: list[dict]) -> list[dict]:
    hits = []
    for t in terms:
        for src in [t.get("source", "")] + list(t.get("aliases") or []):
            if src and src in line_text:
                hits.append(t)
                break
    return hits


def build_batch_messages(batch_start: int, batch_items: list[tuple[int, str]],
                         prev_translated: list[str], next_source: list[str],
                         terms: list[dict], source_lang_name: str = "日语") -> list[dict]:
    """组装一批翻译消息。batch_start 为本批第一行的全局行号（从 1 开始）。"""
    sys_prompt = SYSTEM_PROMPT.format(source_lang_name=source_lang_name)
    parts = [sys_prompt]
    if terms:
        term_lines = [f"- {t['source']} → {t['target']}" for t in terms]
        parts.append(TERMS_HEADER + "\n" + "\n".join(term_lines))
    body_lines = [f'{{"i": {idx - batch_start + 1}, "src": {json.dumps(src, ensure_ascii=False)}}}'
                  for idx, src in batch_items]
    parts.append("【待翻译行】（i 为批内序号，从 1 开始）\n" + "\n".join(body_lines))
    user_prompt = "\n\n".join(parts)

    # 上下文窗口：前 3 句已译 + 后 3 句原文（不计入待翻行号）
    ctx = []
    if prev_translated:
        ctx.append("前文（已翻译）：\n" + "\n".join(prev_translated[-3:]))
    if next_source:
        ctx.append("后文（未翻译，仅供参考）：\n" + "\n".join(next_source[:3]))
    if ctx:
        user_prompt += "\n\n" + CONTEXT_HEADER + "\n" + "\n".join(ctx)

    user_prompt += (
        f"\n\n请输出 JSON 数组，共 {len(batch_items)} 条，"
        f'格式：[{{"i": 1, "zh": "..."}}, ...]，i 从 1 到 {len(batch_items)}。'
    )
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]


class TranslateEngine:
    def __init__(self, provider: LLMProvider, batch_size: int = 15, context_lines: int = 3):
        self.provider = provider
        self.batch_size = batch_size
        self.context_lines = context_lines

    async def translate_lines(self, lines: list[dict], terms: list[dict],
                              source_lang_name: str = "日语",
                              max_batch_retry: int = 3) -> TranslateResult:
        """lines: [{"index": 1, "source_text": "..."}]（index 该集内行号从 1 开始）

        terms: [{"source": ..., "target": ..., "aliases": [...], "locked": bool}]
        返回逐行译文与 token 用量；最终仍失败的行进入 failed_indices。
        """
        result = TranslateResult()
        translated_texts: dict[int, str] = {}
        pending = list(lines)
        rounds = 0
        while pending and rounds < max_batch_retry:
            rounds += 1
            if rounds > 1:
                logger.info(f"翻译第 {rounds - 1} 轮后有 {len(pending)} 行缺失，补齐重试")
            for i in range(0, len(pending), self.batch_size):
                batch = pending[i:i + self.batch_size]
                batch_res = await self._translate_batch(batch, lines, translated_texts, terms,
                                                        source_lang_name)
                translated_texts.update(batch_res.translations)
                result.prompt_tokens += batch_res.prompt_tokens
                result.completion_tokens += batch_res.completion_tokens
                result.cost += batch_res.cost
            pending = [l for l in pending if l["index"] not in translated_texts]
        result.translations = {
            l["index"]: translated_texts[l["index"]] for l in lines if l["index"] in translated_texts
        }
        result.failed_indices = [l["index"] for l in lines if l["index"] not in translated_texts]
        return result

    async def _translate_batch(self, batch: list[dict], all_lines: list[dict],
                               done: dict[int, str], terms: list[dict],
                               source_lang_name: str) -> TranslateResult:
        batch_start = batch[0]["index"]
        # 上下文窗口：前 context_lines 句（已译则带译文）+ 后 context_lines 句原文
        first = batch[0]["index"]
        prev_translated: list[str] = []
        for l in all_lines:
            if l["index"] >= first:
                break
            prev_translated.append(
                f"{l['source_text']} → {done.get(l['index'], '（未译）')}"
            )
        prev_translated = prev_translated[-self.context_lines:]
        last = batch[-1]["index"]
        next_source = [
            l["source_text"] for l in all_lines
            if last < l["index"] <= last + self.context_lines
        ]
        # 批内命中的术语（任一行命中即注入）
        batch_text = "\n".join(l["source_text"] for l in batch)
        hit_terms = _match_terms(batch_text, terms)

        messages = build_batch_messages(
            batch_start,
            [(l["index"], l["source_text"]) for l in batch],
            prev_translated, next_source, hit_terms, source_lang_name,
        )
        # 8192：给推理型模型（GLM/Qwen 思考系列）留足思考+输出空间
        res = TranslateResult()
        for attempt in range(3):
            try:
                resp = await self.provider.chat(messages, json_mode=True, max_tokens=8192)
                res.prompt_tokens += resp.prompt_tokens
                res.completion_tokens += resp.completion_tokens
                data = parse_json_forgiving(resp.content)
                # 容错：数组形式 [{i,zh},…] / 对象形式 {i,zh}（单行批次常见） / {translations:[…]}
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("translations") or data.get("items") or []
                    if not items and ("i" in data or "index" in data):
                        items = [data]
                else:
                    items = []
                by_i: dict[int, str] = {}
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    try:
                        i = int(it.get("i", it.get("index")))
                    except (TypeError, ValueError):
                        continue
                    zh = str(it.get("zh", it.get("target", ""))).strip()
                    if zh:
                        by_i[i] = zh
                # 行号容错映射回本批 index（批内序号 1..n）
                for offset, l in enumerate(batch, start=1):
                    zh = by_i.get(offset)
                    if zh:
                        res.translations[l["index"]] = zh
                missing = [l["index"] for l in batch if l["index"] not in res.translations]
                if not missing:
                    break
                logger.warning(f"批 {batch_start} 缺 {len(missing)} 行：{missing[:5]}…，第 {attempt + 1} 次重试")
            except LLMError as e:
                logger.warning(f"批 {batch_start} 翻译失败：{e}")
                if attempt == 2:
                    break
        res.cost = estimate_cost(res.prompt_tokens, res.completion_tokens,
                                 getattr(self.provider.channel, "price_in_per_m", 0.0),
                                 getattr(self.provider.channel, "price_out_per_m", 0.0))
        return res
