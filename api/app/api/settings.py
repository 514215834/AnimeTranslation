"""系统设置 API：本地路径与 ASR 运行参数的读取/保存（不能写死在代码里）。

- GET  /settings  返回当前生效配置 + 路径状态
- PUT  /settings  保存到 runtime_config.json（优先级高于环境变量）；
                  ffmpeg/ffprobe/ASR 参数保存即生效（重建 Provider 时读取），
                  data_dir / database_url / models_dir 需重启。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings, save_runtime_config

router = APIRouter(prefix="/settings", tags=["settings"])

# 保存后需要重启才完全生效的字段
RESTART_REQUIRED = ["data_dir", "database_url", "models_dir"]
ALLOWED_DEVICE = {"auto", "cuda", "cpu"}
ALLOWED_COMPUTE = {"auto", "int8", "int8_float16", "float16", "int8_float32", "bfloat16"}


class SettingsUpdate(BaseModel):
    data_dir: str | None = None
    database_url: str | None = None
    models_dir: str | None = None
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    hf_endpoint: str | None = None
    asr_device: str | None = None
    asr_compute_type: str | None = None
    asr_beam_size: int | None = Field(default=None, ge=1, le=8)


class PathStatus(BaseModel):
    path: str
    exists: bool
    is_dir: bool


class SettingsOut(BaseModel):
    data_dir: str
    database_url: str
    models_dir: str
    ffmpeg_path: str
    ffprobe_path: str
    ffmpeg_found: bool
    ffprobe_found: bool
    hf_endpoint: str
    asr_device: str
    asr_compute_type: str
    asr_beam_size: int
    derived: dict[str, PathStatus]  # media / artifacts / exports
    restart_required_fields: list[str]


def _status(p: Path) -> PathStatus:
    try:
        return PathStatus(path=str(p), exists=p.exists(), is_dir=p.is_dir())
    except OSError:
        return PathStatus(path=str(p), exists=False, is_dir=False)


def _payload() -> SettingsOut:
    s = get_settings()
    ffmpeg_ok = Path(s.ffmpeg_path).is_file()
    ffprobe_ok = Path(s.ffprobe_path).is_file()
    return SettingsOut(
        data_dir=str(s.data_dir),
        database_url=s.database_url,
        models_dir=str(s.resolved_models_dir),
        ffmpeg_path=s.ffmpeg_path,
        ffprobe_path=s.ffprobe_path,
        ffmpeg_found=ffmpeg_ok,
        ffprobe_found=ffprobe_ok,
        hf_endpoint=s.hf_endpoint,
        asr_device=s.asr_device,
        asr_compute_type=s.asr_compute_type,
        asr_beam_size=s.asr_beam_size,
        derived={
            "媒体目录 media": _status(s.media_dir),
            "任务产物 artifacts": _status(s.artifacts_dir),
            "导出成品 exports": _status(s.exports_dir),
        },
        restart_required_fields=RESTART_REQUIRED,
    )


@router.get("", response_model=SettingsOut)
async def get_app_settings():
    return _payload()


@router.put("", response_model=SettingsOut)
async def update_app_settings(body: SettingsUpdate):
    updates = body.model_dump(exclude_none=True)

    # ---- 校验 ----
    for key in ("data_dir", "models_dir"):
        if key in updates:
            p = Path(updates[key])
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise HTTPException(422, f"{key} 无法创建目录 {p}：{e}")
    for key in ("ffmpeg_path", "ffprobe_path"):
        if key in updates and updates[key]:
            if not Path(updates[key]).is_file():
                raise HTTPException(422, f"{key} 指向的文件不存在：{updates[key]}（留空则自动从 PATH 查找）")
    if "asr_device" in updates and updates["asr_device"] not in ALLOWED_DEVICE:
        raise HTTPException(422, f"asr_device 取值须为 {sorted(ALLOWED_DEVICE)}")
    if "asr_compute_type" in updates and updates["asr_compute_type"] not in ALLOWED_COMPUTE:
        raise HTTPException(422, f"asr_compute_type 取值须为 {sorted(ALLOWED_COMPUTE)}")
    if "database_url" in updates:
        url = updates["database_url"]
        if not url.startswith("sqlite+aiosqlite:///"):
            raise HTTPException(422, "M1 仅支持 SQLite：sqlite+aiosqlite:///…（PostgreSQL 在 v1.0 引入）")

    save_runtime_config(updates)
    get_settings.cache_clear()  # ffmpeg/ASR 参数立即生效
    return _payload()
