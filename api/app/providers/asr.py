"""ASR Provider：faster-whisper。未安装时抛 ASRError，由流水线转为明确报错。

接口约定（架构文档 4.1）：transcribe(audio_path, language) -> [{start_ms, end_ms, text, confidence}]

GPU 提示：1050 Ti 等 Pascal 显卡不支持原生 fp16 计算，默认走 int8 量化
（CTRanslate2 在 CC 6.1 上用 dp4a 指令，4GB 显存可跑 medium，large-v3 需降 beam）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscriptLine:
    start_ms: int
    end_ms: int
    text: str
    confidence: float = 1.0


class ASRError(RuntimeError):
    pass


def _add_windows_nvidia_dll_dirs() -> None:
    """加载 pip 安装的 nvidia-cublas/cudnn wheel 里的 CUDA 运行库。

    系统 CUDA toolkit 已装时无此目录，静默跳过。
    用 ctypes 按绝对路径预加载：进程内同名模块加载后，ctranslate2 的
    LoadLibrary("cublas64_12.dll") 会直接复用，绕开搜索路径问题。
    """
    if sys.platform != "win32":
        return
    import ctypes
    import sysconfig

    site = sysconfig.get_paths().get("purelib")
    if not site:
        return
    dll_files: list[str] = []
    for pkg in ("cublas", "cudnn"):
        d = Path(site) / "nvidia" / pkg / "bin"
        if d.is_dir():
            os.add_dll_directory(str(d))
            dll_files.extend(str(f) for f in d.glob("*.dll"))
    # 两轮预加载：首轮可能有依赖顺序失败，第二轮补齐
    for _ in range(2):
        failed: list[str] = []
        for f in dll_files:
            try:
                ctypes.WinDLL(f)
            except OSError:
                failed.append(f)
        dll_files = failed
        if not dll_files:
            break


class ASRProvider:
    """faster-whisper 封装；模型大小/语言/设备可配（F-M1-03）。"""

    def __init__(self, model_size: str = "small", device: str = "auto",
                 models_dir: str | Path | None = None,
                 compute_type: str = "auto", beam_size: int = 5):
        self.model_size = model_size
        self.device = device
        self.models_dir = str(models_dir) if models_dir else None
        # auto：GPU/CPU 均取 int8 —— Pascal 无原生 fp16，且 int8 对 4GB 显存最友好
        self.compute_type = "int8" if compute_type in ("auto", "") else compute_type
        self.beam_size = beam_size
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise ASRError(
                    "faster-whisper 未安装。请执行: pip install -e '.[asr]' "
                    "(api 目录下)，或使用 Docker 镜像（内置 asr 依赖）"
                ) from e
            _add_windows_nvidia_dll_dirs()
            # 下载/加载目录：MODELS_DIR/faster-whisper（默认 data/models/faster-whisper）
            cache_dir = os.path.join(self.models_dir, "faster-whisper") if self.models_dir else None
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=cache_dir,
                )
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "cublas" in msg.lower() or "cudnn" in msg.lower() or "cuda" in msg.lower():
                    raise ASRError(
                        f"CUDA 运行库加载失败（{msg[:200]}）。"
                        "处理：1) 更新 NVIDIA 驱动；2) pip install nvidia-cublas-cu12 nvidia-cudnn-cu12；"
                        "3) 或在 api/.env 设 ASR_DEVICE=cpu 用 CPU 模式"
                    ) from e
                raise
        return self._model

    def transcribe(self, audio_path: str, language: str = "ja",
                   vad_filter: bool = True,
                   progress_cb=None) -> list[TranscriptLine]:
        model = self._load()
        try:
            segments_iter, info = model.transcribe(
                audio_path,
                language=language,
                vad_filter=vad_filter,
                beam_size=self.beam_size,
            )
            lines: list[TranscriptLine] = []
            for seg in segments_iter:
                lines.append(TranscriptLine(
                    start_ms=int(seg.start * 1000),
                    end_ms=int(seg.end * 1000),
                    text=seg.text.strip(),
                    confidence=float(seg.avg_logprob) if seg.avg_logprob else 1.0,
                ))
                if progress_cb and len(lines) % 20 == 0:
                    progress_cb(len(lines))
            return lines
        except ASRError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "out of memory" in msg or "cuda_error" in msg:
                raise ASRError(
                    "GPU 显存不足。对策（按优先级）：1) 换小模型（medium→small）；"
                    "2) api/.env 设 ASR_BEAM_SIZE=1；3) ASR_DEVICE=cpu。"
                    f"原始错误：{e}"
                ) from e
            raise ASRError(f"ASR 推理失败：{e}") from e


class MockASRProvider(ASRProvider):
    """测试/演示用：读同目录同名 .srt 作为转录结果，不调模型。"""

    def transcribe(self, audio_path: str, language: str = "ja", vad_filter: bool = True,
                   progress_cb=None) -> list[TranscriptLine]:
        import pysubs2

        srt_path = Path(audio_path).with_suffix(".srt")
        if not srt_path.exists():
            raise ASRError(f"MockASR: 找不到 {srt_path}")
        subs = pysubs2.load(str(srt_path))
        return [
            TranscriptLine(start_ms=int(e.start), end_ms=int(e.end), text=e.text.replace("\\N", "\n"))
            for e in subs.events
        ]
