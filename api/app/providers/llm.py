"""LLM Provider：统一 OpenAI 兼容 Chat Completions 协议（架构文档 4.1）。

一套代码接入 GPT / Claude / Gemini / DeepSeek / Qwen / Ollama / SakuraLLM。
- 指数退避重试 + 速率限制（RPM）
- JSON 结构化输出容错解析（针对各家模型 JSON 纪律差的现实）
- token 用量与费用估算返回
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, field

import httpx
from loguru import logger


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


@dataclass
class ChannelConfig:
    id: str
    name: str
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.3
    rpm_limit: int = 0
    timeout_seconds: float = 120.0
    extra_headers: dict[str, str] = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")
_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")


class LLMProvider:
    """单渠道 OpenAI 兼容客户端。"""

    def __init__(self, channel: ChannelConfig, client: httpx.AsyncClient | None = None):
        self.channel = channel
        self._client = client or httpx.AsyncClient(timeout=channel.timeout_seconds)
        self._rpm_lock = asyncio.Lock()
        self._rpm_window: list[float] = []

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle_rpm(self) -> None:
        if self.channel.rpm_limit <= 0:
            return
        async with self._rpm_lock:
            now = time.monotonic()
            self._rpm_window = [t for t in self._rpm_window if now - t < 60.0]
            if len(self._rpm_window) >= self.channel.rpm_limit:
                wait = 60.0 - (now - self._rpm_window[0]) + 0.05
            else:
                wait = 0.0
            self._rpm_window.append(time.monotonic())
        if wait > 0:
            logger.debug(f"[{self.channel.name}] RPM 限流，等待 {wait:.1f}s")
            await asyncio.sleep(wait)

    async def chat(self, messages: list[dict], json_mode: bool = True,
                   max_retries: int = 3, max_tokens: int = 4096) -> LLMResponse:
        url = self.channel.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.channel.model,
            "messages": messages,
            "temperature": self.channel.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.channel.api_key}",
                   **(self.channel.extra_headers or {})}

        last_err: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                await self._throttle_rpm()
            except Exception:  # noqa: BLE001
                pass
            try:
                resp = await self._client.post(url, json=payload, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                if resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")
                data = resp.json()
                usage = data.get("usage") or {}
                choice = (data.get("choices") or [{}])[0]
                content = choice.get("message", {}).get("content", "")
                if not content:
                    finish = choice.get("finish_reason")
                    reasoning = ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0
                    if finish == "length":
                        raise LLMError(
                            f"模型输出被 max_tokens 截断（思考消耗 {reasoning} tokens）——"
                            "推理模型请增大 max_tokens 或减小批次行数"
                        )
                    raise LLMError("响应 content 为空")
                return LLMResponse(
                    content=content,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    model=data.get("model", self.channel.model),
                )
            except (httpx.HTTPError, LLMError, json.JSONDecodeError) as e:
                last_err = e
                if attempt >= max_retries:
                    break
                backoff = min(60.0, 2.0 ** attempt) * (1 + random.random() * 0.3)
                logger.warning(f"[{self.channel.name}] 第 {attempt} 次请求失败：{e}；{backoff:.1f}s 后重试")
                await asyncio.sleep(backoff)
        raise LLMError(f"渠道[{self.channel.name}] 重试 {max_retries} 次仍失败：{last_err}")


# ---------- JSON 容错解析（F-M1-04 行号容错） ----------

def parse_json_forgiving(content: str) -> dict | list:
    """尽力解析 LLM 返回的 JSON：剥代码栅栏、截取首段数组/对象、单引号修复。"""
    text = content.strip()
    # 剥 ```json ... ``` 栅栏
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 截取最外层 JSON
    for pattern in (_JSON_ARRAY_RE, _JSON_OBJ_RE):
        m = pattern.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                # 常见小错：尾逗号
                cleaned = re.sub(r",\s*([\]}])", r"\1", m.group(0))
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass
    raise LLMError(f"无法从 LLM 响应解析 JSON：{content[:200]}")


def estimate_cost(prompt_tokens: int, completion_tokens: int,
                  price_in_per_m: float, price_out_per_m: float) -> float:
    return (prompt_tokens / 1_000_000) * price_in_per_m + (completion_tokens / 1_000_000) * price_out_per_m
