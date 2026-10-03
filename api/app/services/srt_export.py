"""SRT 导出（F-M1-06）：单语（中文）与双语（日上中下）。基于 pysubs2。"""
from __future__ import annotations

from pathlib import Path

import pysubs2


def export_srt(segments: list[dict], out_path: str, bilingual: bool = False) -> str:
    """segments: [{index, start_ms, end_ms, source_text, target_text}]（按 index 升序）

    bilingual=True 时同一时间轴事件内两行：原文（上）+ 译文（下）。
    空译文的行：单语导出时跳过；双语导出时仅保留原文。
    """
    subs = pysubs2.SSAFile()
    for seg in segments:
        target = (seg.get("target_text") or "").strip()
        source = (seg.get("source_text") or "").strip()
        if not bilingual and not target:
            continue
        lines = []
        if bilingual:
            if source:
                lines.append(source)
            if target:
                lines.append(target)
        else:
            lines.append(target)
        if not lines:
            continue
        event = pysubs2.SSAEvent(
            start=int(seg["start_ms"]),
            end=max(int(seg["end_ms"]), int(seg["start_ms"]) + 1),
            text="\\N".join(lines),
        )
        subs.events.append(event)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(out), format_="srt")
    return str(out)


def export_srt_target(segments: list[dict], out_path: str) -> str:
    return export_srt(segments, out_path, bilingual=False)


def export_srt_bilingual(segments: list[dict], out_path: str) -> str:
    return export_srt(segments, out_path, bilingual=True)
