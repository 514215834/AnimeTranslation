"""FFmpeg 媒体处理（F-M1-01/02）：探测元信息、抽取 16k 单声道音轨。

一切音视频操作收敛到 FFmpeg（架构文档 2.2）。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.config import get_settings


class MediaError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    duration_ms: int
    width: int | None
    height: int | None
    audio_tracks: list[dict]  # [{index, codec, language, channels}]


def _tool(name_env_resolved: str, flag: str) -> str:
    path = Path(name_env_resolved)
    if path.is_file():
        return str(path)
    raise MediaError(f"未找到 {Path(name_env_resolved).name}（{flag}）。"
                     f"请安装 FFmpeg 并加入 PATH，或设置 {flag} 环境变量指向可执行文件")


async def _run(cmd: list[str], timeout: float = 3600.0) -> bytes:
    logger.debug(" ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise MediaError(f"FFmpeg 执行超时（>{timeout}s）") from e
    if proc.returncode != 0:
        tail = err.decode("utf-8", "ignore").strip().splitlines()[-6:]
        raise MediaError("FFmpeg 失败: " + " | ".join(tail))
    return out


async def probe(video_path: str) -> MediaInfo:
    """ffprobe 解析时长/分辨率/音轨（F-M1-01）。"""
    settings = get_settings()
    ffprobe = _tool(settings.ffprobe_path, "FFPROBE_PATH")
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", video_path,
    ]
    out = await _run(cmd, timeout=120)
    data = json.loads(out.decode("utf-8", "ignore"))

    fmt = data.get("format", {})
    duration_ms = int(float(fmt.get("duration") or 0) * 1000)
    width = height = None
    audio_tracks: list[dict] = []
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and width is None:
            width = st.get("width")
            height = st.get("height")
        elif st.get("codec_type") == "audio":
            tags = st.get("tags") or {}
            audio_tracks.append({
                "index": st.get("index"),
                "codec": st.get("codec_name"),
                "language": tags.get("language", ""),
                "channels": st.get("channels"),
            })
    if not audio_tracks:
        raise MediaError("片源中没有音轨，无法进行语音识别")
    if duration_ms <= 0:
        raise MediaError("无法解析视频时长，文件可能损坏或编码异常")
    return MediaInfo(duration_ms=duration_ms, width=width, height=height, audio_tracks=audio_tracks)


async def extract_audio(video_path: str, out_wav: str) -> str:
    """抽取音轨重采样为 16k 单声道 PCM wav（F-M1-02 / S1 ingest 产物）。"""
    settings = get_settings()
    ffmpeg = _tool(settings.ffmpeg_path, "FFMPEG_PATH")
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-i", video_path, "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-f", "wav", out_wav,
    ]
    await _run(cmd)
    if not Path(out_wav).exists() or Path(out_wav).stat().st_size == 0:
        raise MediaError("音轨抽取产物为空，可能是异常编码")
    return out_wav
