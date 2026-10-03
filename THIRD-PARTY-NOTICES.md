# THIRD-PARTY-NOTICES 第三方声明

> 依《技术架构文档》第 12.4 节发布要求维护。核查日期：2026-10-02（发版前需重新执行 `pip-licenses` 核查）。
> 结论：M1 运行时依赖全部为宽松许可（MIT / BSD / Apache-2.0 / MPL-2.0 / PSF），无 GPL/AGPL/NC 传染项。

## 后端 Python 依赖（运行时）

| 包 | 许可证 |
|---|---|
| fastapi / starlette | MIT / BSD-3-Clause |
| uvicorn（及 httptools/watchfiles/websockets） | BSD-3-Clause |
| pydantic / pydantic-settings / annotated-types / typing-extensions | MIT / PSF-2.0 |
| SQLAlchemy / greenlet / aiosqlite | MIT（greenlet 含 PSF-2.0） |
| httpx / httpcore / h11 / anyio / sniffio / idna / certifi | BSD / MIT / MPL-2.0 |
| python-multipart | Apache-2.0 |
| loguru | MIT |
| pysubs2 | MIT |
| cryptography / cffi | Apache-2.0 OR BSD-3-Clause / MIT-0 |
| python-dotenv | BSD-3-Clause |
| click / colorama / packaging | BSD / Apache-2.0 OR BSD-2-Clause |

开发依赖：pytest（MIT）、pytest-asyncio（Apache-2.0）、pip-licenses（MIT）。

ASR 可选依赖（`.[asr]`，用户自装/用户下载模型）：**faster-whisper（MIT，模型权重随源）**；ctranslate2（MIT）。
NC 权重红线：Sakura 系列权重（CC BY-NC-SA 4.0）、fish-speech 权重不进入任何镜像/发布物。

## 前端 npm 依赖

| 包 | 许可证 |
|---|---|
| vue / vue-router / pinia | MIT |
| element-plus（含 @element-plus/icons-vue） | MIT |
| axios | MIT |
| dayjs | MIT |
| vite / @vitejs/plugin-vue / typescript | MIT / Apache-2.0 |

## 系统级组件

- **FFmpeg**：LGPL-2.1+（本项目仅以独立进程/子进程方式调用，不链接其代码；发行版按 gyan.dev full build 的组件清单执行）。
- **faster-whisper 模型权重**（运行时由用户下载）：基于 openai/whisper 的开源权重衍生，MIT 系。

## 参考/借鉴声明（零代码复制）

以下项目仅借鉴设计思路，未复制任何代码：pyvideotrans（GPL-3.0）、GalTransl（GPL-3.0）、SmartSub（MIT）、llm-subtrans（MIT）、AutoSubtitle（无 LICENSE，仅交互参考）、Bangumi api（无 LICENSE，数据商用需换源，见架构文档 12.2）。
