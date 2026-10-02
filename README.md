# AnimeTranslation · AI 动漫番剧翻译平台

面向字幕组与个人译者的"生肉 → 精翻熟肉"AI 辅助翻译平台。以日语 → 中文为主，架构支持多语言扩展。核心原则：**AI 提效、人把质量关**——ASR / OCR / LLM 产出高质量初稿，译者在 Web 复核工作台完成校对终审。

## 核心流水线

```
生肉视频 → 媒体接入(FFmpeg) → 语音识别(faster-whisper) → 硬字幕OCR(PaddleOCR, 可选)
        → AI 翻译(LLM 双轮: 初翻→润色) → 字幕工程(SRT/ASS) → 质检 QA
        → 人工复核(Web 工作台) → 交付(软字幕/硬压/纯字幕文件)
```

## 迭代路线

| 里程碑 | 版本 | 主题 |
|---|---|---|
| M1 | v0.1 | MVP：单集"生肉→中文字幕"闭环 |
| M2 | v0.2 | 字幕工程与翻译质量（双轮翻译、术语表、Bangumi 集成、批量） |
| M3 | v1.0 | 人工复核工作台与团队协作 |
| M4 | v1.x | 生态与开放能力（插件 / API / CLI / 多语言） |

## 技术栈

Python 3.11 + FastAPI ｜ Vue 3 + Vite + Element Plus + artplayer ｜ faster-whisper ｜ PaddleOCR ｜ OpenAI 兼容 LLM 网关（GPT / Claude / Gemini / DeepSeek / Qwen / Ollama / SakuraLLM）｜ pysubs2 + FFmpeg ｜ Docker Compose

## 文档

- [技术架构文档](docs/技术架构文档.md)
- [功能点迭代文档](docs/功能点迭代文档.md)

## 合规说明

本项目仅用于处理用户自有授权素材，不内置任何片源站点抓取能力。
