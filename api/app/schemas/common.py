"""Pydantic v2 请求/响应模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------- Project ----------

class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    source_lang: str = "ja"
    target_lang: str = "zh-Hans"


class ProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    source_lang: str | None = None
    target_lang: str | None = None
    status: str | None = None  # active | archived


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    description: str
    source_lang: str
    target_lang: str
    status: str
    created_at: datetime
    episode_count: int = 0


# ---------- Episode ----------

class EpisodeCreate(BaseModel):
    number: int = Field(ge=1)
    title: str = ""


class EpisodeUpdate(BaseModel):
    title: str | None = None
    number: int | None = None


class EpisodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    number: int
    title: str
    video_name: str
    video_path: str
    duration_ms: int | None
    width: int | None
    height: int | None
    audio_tracks: list | None
    status: str
    error: str
    segment_count: int = 0
    translated_count: int = 0


# ---------- Job ----------

class JobCreate(BaseModel):
    """启动流水线。stages 为空 = 全部；指定子集可断点重跑（如 ["translate","subtitle","export"]）。"""
    stages: list[str] = []
    asr_model: str = "small"
    asr_language: str = "ja"
    channel_id: str | None = None


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    stage: str
    state: str
    artifact_path: str
    error: str
    retry_count: int
    started_at: datetime | None
    finished_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    episode_id: str
    state: str
    current_stage: str | None
    config: dict
    error: str
    token_cost_json: dict
    created_at: datetime
    finished_at: datetime | None
    stages: list[StageOut] = []


class RetryRequest(BaseModel):
    stage: str


# ---------- Segment ----------

class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    episode_id: str
    index: int
    track_type: str
    start_ms: int
    end_ms: int
    source_text: str
    target_text: str
    speaker: str
    status: str
    locked: bool
    qc_flags: list


class SegmentUpdate(BaseModel):
    target_text: str | None = None
    source_text: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    locked: bool | None = None
    status: str | None = None


# ---------- Glossary ----------

class GlossaryTermCreate(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    aliases: list[str] = []
    note: str = ""
    locked: bool = False
    project_id: str | None = None  # None = 全局


class GlossaryTermUpdate(BaseModel):
    source: str | None = None
    target: str | None = None
    aliases: list[str] | None = None
    note: str | None = None
    locked: bool | None = None


class GlossaryTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str | None
    source: str
    target: str
    aliases: list
    note: str
    locked: bool


# ---------- LLM Channel ----------

class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=300)
    api_key: str = ""
    model: str = Field(min_length=1, max_length=200)
    temperature: float = 0.3
    rpm_limit: int = 0
    price_in_per_m: float = 0.0
    price_out_per_m: float = 0.0
    enabled: bool = True
    extra_headers: dict[str, str] = {}  # 网关要求的自定义请求头（如 x-opencode-session）


class ChannelUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # 传空串表示不改
    model: str | None = None
    temperature: float | None = None
    rpm_limit: int | None = None
    price_in_per_m: float | None = None
    price_out_per_m: float | None = None
    enabled: bool | None = None
    extra_headers: dict[str, str] | None = None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    base_url: str
    model: str
    temperature: float
    rpm_limit: int
    price_in_per_m: float
    price_out_per_m: float
    enabled: bool
    extra_headers: dict = {}
    api_key_masked: str = ""


class ChannelTestOut(BaseModel):
    ok: bool
    message: str
    latency_ms: int = 0
    reply: str = ""
