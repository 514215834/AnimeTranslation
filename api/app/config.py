"""应用配置：三层优先级 runtime_config.json（界面写入）> 环境变量/.env > 默认值。

涉及本地路径的配置项全部可配（界面「系统设置」页 / RUNTIME_CONFIG 文件 / 环境变量）：
- data_dir       数据总目录（media/ artifacts/ exports/ 派生自它）   [改后需重启]
- database_url   SQLite 文件路径（sqlite+aiosqlite:///…）            [改后需重启]
- models_dir     whisper 模型缓存目录（None=随 data_dir/models）     [改后需重启]
- ffmpeg_path / ffprobe_path   可执行文件（留空=从 PATH 查找）        [保存即生效]
- hf_endpoint    HuggingFace 镜像（模型下载）                          [新进程生效]
- asr_device / asr_compute_type / asr_beam_size                        [保存即生效]
"""
from __future__ import annotations

import json
import os
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent  # api/app/config.py → api/
PROJECT_ROOT = BACKEND_ROOT.parent


def runtime_config_path() -> Path:
    return Path(os.environ.get("RUNTIME_CONFIG_PATH", BACKEND_ROOT / "runtime_config.json"))


def load_runtime_config() -> dict:
    p = runtime_config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_runtime_config(updates: dict) -> None:
    """界面保存：合并写回（只存显式设置过的项，未设项回落到环境变量/默认值）。"""
    p = runtime_config_path()
    cfg = load_runtime_config()
    for k, v in updates.items():
        if v is None or v == "":
            cfg.pop(k, None)  # 清空 = 回落自动
        else:
            cfg[k] = v
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AnimeTranslation API"
    version: str = "0.1.0"
    api_prefix: str = "/api/v1"

    # 数据总目录：SQLite 默认库文件 / 媒体 / 产物 / 导出的根（media 等为其子目录）
    data_dir: Path = BACKEND_ROOT / "data"
    database_url: str = f"sqlite+aiosqlite:///{(BACKEND_ROOT / 'data' / 'app.db').as_posix()}"
    # whisper 模型缓存目录；None = data_dir/models
    models_dir: Path | None = None

    # FFmpeg / ffprobe 可执行文件；留空 = 从 PATH 查找
    ffmpeg_path: str = ""
    ffprobe_path: str = ""

    # HuggingFace 镜像（模型下载），如 https://hf-mirror.com
    hf_endpoint: str = ""

    # ASR 运行参数。1050 Ti 等 Pascal 显卡不支持原生 fp16（计算速率 1/64），
    # 必须用 int8 —— 因此 auto 在 GPU 上也解析为 int8（4GB 显存友好）
    asr_device: str = "auto"        # auto | cuda | cpu
    asr_compute_type: str = "auto"  # auto → cuda: int8 / cpu: int8
    asr_beam_size: int = 5

    # Fernet 密钥用于加密落库渠道 API Key；未配置时由 _ensure_secrets() 生成并写入 api/.env
    fernet_key: str = ""

    upload_max_mb: int = 4096
    allowed_upload_exts: list[str] = [".mp4", ".mkv", ".flv", ".avi", ".mov", ".wmv", ".webm", ".ts"]

    # 人人能跑的默认翻译批大小
    translate_batch_size: int = 15
    context_lines: int = 3

    # 前端构建产物（生产托管用）——固定在项目根 web/dist，不随 data_dir 移动
    frontend_dist: Path = PROJECT_ROOT / "web" / "dist"

    # ---- 派生目录（随 data_dir 联动，无需单独配置） ----

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def resolved_models_dir(self) -> Path:
        return self.models_dir if self.models_dir else self.data_dir / "models"


def _resolve_tool(configured: str, name: str, env_var: str) -> str:
    """解析可执行文件：界面/环境变量显式路径 > PATH 查找。找不到时原样返回，由调用方报错。"""
    if configured:
        return configured
    custom = os.environ.get(env_var)
    if custom:
        return custom
    found = shutil.which(name)
    return found or name


def _ensure_secrets(s: Settings) -> None:
    """首次启动生成 fernet_key 持久化到 api/.env，避免重启后旧密文解不开。"""
    if s.fernet_key:
        return
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    env_path = BACKEND_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    lines = [l for l in lines if not l.startswith("FERNET_KEY=")]
    lines.append(f"FERNET_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    s.fernet_key = key


@lru_cache
def get_settings() -> Settings:
    # 界面保存的 runtime_config.json 显式覆盖环境变量
    s = Settings(**load_runtime_config())
    if not s.database_url:
        s.database_url = f"sqlite+aiosqlite:///{(BACKEND_ROOT / 'data' / 'app.db').as_posix()}"
    s.ffmpeg_path = _resolve_tool(s.ffmpeg_path, "ffmpeg", "FFMPEG_PATH")
    s.ffprobe_path = _resolve_tool(s.ffprobe_path, "ffprobe", "FFPROBE_PATH")
    for d in (s.data_dir, s.media_dir, s.artifacts_dir, s.exports_dir, s.resolved_models_dir):
        Path(d).mkdir(parents=True, exist_ok=True)
    if s.hf_endpoint:
        os.environ.setdefault("HF_ENDPOINT", s.hf_endpoint)
    _ensure_secrets(s)
    return s


settings = get_settings
