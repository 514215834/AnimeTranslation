# AnimeTranslation · AI 动漫番剧翻译平台

面向字幕组与个人译者的"生肉 → 精翻熟肉"AI 辅助翻译平台。以日语 → 中文为主，架构支持多语言扩展。核心原则：**AI 提效、人把质量关**——ASR / OCR / LLM 产出高质量初稿，译者在 Web 复核工作台完成校对终审。

## 核心流水线

```
生肉视频 → 媒体接入(FFmpeg) → 语音识别(faster-whisper) → 硬字幕OCR(PaddleOCR, 可选)
        → AI 翻译(LLM 双轮: 初翻→润色) → 字幕工程(SRT/ASS) → 质检 QA
        → 人工复核(Web 工作台) → 交付(软字幕/硬压/纯字幕文件)
```

## 迭代路线

| 里程碑 | 版本 | 主题 | 状态 |
|---|---|---|---|
| M1 | v0.1 | MVP：单集"生肉→中文字幕"闭环 | **已实现（见下）** |
| M2 | v0.2 | 字幕工程与翻译质量（双轮翻译、术语表、Bangumi 集成、批量） | 未开始 |
| M3 | v1.0 | 人工复核工作台与团队协作 | 未开始 |
| M4 | v1.x | 生态与开放能力（插件 / API / CLI / 多语言） | 未开始 |

## 快速开始

### 方式 A：Docker Compose（推荐，含 ASR 依赖）

前置：Docker、能访问模型下载源。

```bash
docker compose up -d --build
# 浏览器打开 http://localhost:8080
```

首次跑 ASR 时 faster-whisper 会自动下载模型到 `data/models/`（small 约 460MB，large-v3 约 3GB）。

### 方式 B：本地开发（本机实际部署：G:\DeskTop）

前置：Python ≥ 3.11、Node ≥ 18、FFmpeg（在 PATH 或设 `FFMPEG_PATH`/`FFPROBE_PATH`）。

本机资源布局（`api/.env` 已配置）：venv / 模型 / 数据全部在 `G:\DeskTop\AnimeTranslation\`：

```
G:\DeskTop\AnimeTranslation\
├── venv\      Python 虚拟环境（含 faster-whisper）
├── models\    whisper 模型缓存（MODELS_DIR）
│   └── faster-whisper\   small 464MB / medium 1.5GB / large-v3 2.9GB 已下载
└── data\      app.db / media / artifacts / exports（DATA_DIR）
```

```bash
# 1) 后端（用 G:\DeskTop 的 venv）
cd api
G:\DeskTop\AnimeTranslation\venv\Scripts\uvicorn app.main:app --port 8000

# 2) 前端（开发模式）
cd ../web
npm install
npm run dev         # http://localhost:5173，已代理 /api 到 8000
```

生产托管：`npm run build` 后由 FastAPI 自动托管 `web/dist`（访问 http://127.0.0.1:8000）。

#### GPU（1050 Ti 4GB）实测结论

CTranslate2 int8 量化（`api/.env`：`ASR_DEVICE=auto` + `ASR_COMPUTE_TYPE=auto`，Pascal 卡无原生 fp16，勿设 float16）：

| 模型 | 实测显存 | 速度（30s 音频） | 说明 |
|---|---|---|---|
| **large-v3** | 1.6GB | 1.8s（17x 实时） | 质量最高，**推荐默认** |
| medium | 0.8GB | 1.0s（30x 实时） | 快速模式 |
| small | 更低 | 更快 | CPU 模式兜底（`ASR_DEVICE=cpu`） |

显存吃紧或 OOM 时：`ASR_BEAM_SIZE=1` → 换 medium → `ASR_DEVICE=cpu`。模型走 hf-mirror（`HF_ENDPOINT`）自动下载到 `MODELS_DIR`。

### 首次使用（Web 界面）

1. **翻译渠道**页 → 添加 OpenAI 兼容渠道（如 DeepSeek：`https://api.deepseek.com/v1` + sk-… + `deepseek-chat`），点「测试」验证连通。
2. **术语表**页 → 维护人名/招式名词条（可选，翻译时强制注入）。
3. **番剧项目** → 新建项目 → 添加剧集 → 上传视频（mp4/mkv，自动解析时长/分辨率/音轨）。
4. 剧集页 → 启动流水线（可选 ASR 模型 small/medium/large-v3、源语言、翻译渠道）→ 观察五阶段进度（媒体接入 → 语音识别 → LLM 翻译 → 字幕生成 → 交付）。
5. 完成后下载 **单语 SRT**（`E01.zh.srt`）或 **双语 SRT**（`E01.bilingual.srt`，日上中下）；译文中可直接行内编辑。

### 运行时数据布局

```
api/data/
├── app.db        SQLite 元数据
├── media/        上传视频 + 抽取的 16k mono wav
├── artifacts/    各 Stage 产物（断点续跑：重跑只执行失败 Stage）
├── exports/      导出成品 SRT
└── models/       faster-whisper 模型缓存
```

### 常用配置

**所有本地路径均可在 Web 界面配置**（右上角「系统设置」），保存到 `api/runtime_config.json`（该文件优先级高于环境变量，已 gitignore）。配置项与生效方式：

| 配置项 | 生效方式 | 说明 |
|---|---|---|
| 数据总目录 `data_dir` | 需重启 | media/artifacts/exports 派生自它；改动后需迁移旧目录内容 |
| whisper 模型目录 `models_dir` | 需重启 | 留空 = 数据总目录/models |
| 数据库文件 `database_url` | 需重启 | M1 为 SQLite（`sqlite+aiosqlite:///…`） |
| FFmpeg / FFprobe 路径 | 即时生效 | 留空自动从 PATH 查找 |
| HF 镜像 `hf_endpoint` | 新进程生效 | 如 https://hf-mirror.com |
| ASR 设备 / 量化 / beam | 即时生效 | auto = GPU 用 int8（Pascal 4GB 卡友好） |

环境变量（`FFMPEG_PATH`、`DATA_DIR` 等）与 `api/.env` 仍可用作引导层默认值；`FERNET_KEY` 只存 .env（密钥不入界面）。

### 测试

```bash
cd api
.venv/Scripts/python -m pytest tests/ -q
# 19 个用例：翻译引擎容错（缺行补齐/JSON 容错）、流水线状态机（断点续跑/单 Stage 重试）、
# SRT 双语导出、术语注入、HTTP API 全流程 e2e
```

## 技术栈

Python 3.11 + FastAPI ｜ Vue 3 + Vite + Element Plus + artplayer ｜ faster-whisper ｜ PaddleOCR ｜ OpenAI 兼容 LLM 网关（GPT / Claude / Gemini / DeepSeek / Qwen / Ollama / SakuraLLM）｜ pysubs2 + FFmpeg ｜ Docker Compose

## 文档

- [技术架构文档](docs/技术架构文档.md)
- [功能点迭代文档](docs/功能点迭代文档.md)

## 合规说明

本项目仅用于处理用户自有授权素材，不内置任何片源站点抓取能力。第三方依赖许可核查要求见技术架构文档第 12 节。
