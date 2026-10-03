import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1', timeout: 600000 })

api.interceptors.response.use(
  (r) => r,
  (err) => {
    const msg = err.response?.data?.detail ?? err.message ?? '请求失败'
    return Promise.reject(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)))
  },
)

// ---------- 类型 ----------

export interface Project {
  id: string
  title: string
  description: string
  source_lang: string
  target_lang: string
  status: string
  created_at: string
  episode_count: number
}

export interface Episode {
  id: string
  project_id: string
  number: number
  title: string
  video_name: string
  video_path: string
  duration_ms: number | null
  width: number | null
  height: number | null
  audio_tracks: { index: number; codec: string; language: string; channels: number }[] | null
  status: string
  error: string
  segment_count: number
  translated_count: number
}

export interface StageRun {
  id: string
  stage: string
  state: string
  artifact_path: string
  error: string
  retry_count: number
}

export interface Job {
  id: string
  episode_id: string
  state: string
  current_stage: string | null
  config: Record<string, any>
  error: string
  token_cost_json: Record<string, any>
  created_at: string
  finished_at: string | null
  stages: StageRun[]
}

export interface Segment {
  id: string
  episode_id: string
  index: number
  track_type: string
  start_ms: number
  end_ms: number
  source_text: string
  target_text: string
  speaker: string
  status: string
  locked: boolean
  qc_flags: string[]
}

export interface GlossaryTerm {
  id: string
  project_id: string | null
  source: string
  target: string
  aliases: string[]
  note: string
  locked: boolean
}

export interface Channel {
  id: string
  name: string
  base_url: string
  model: string
  temperature: number
  rpm_limit: number
  price_in_per_m: number
  price_out_per_m: number
  enabled: boolean
  extra_headers: Record<string, string>
  api_key_masked: string
}

// ---------- API ----------

export const listProjects = () => api.get<Project[]>('/projects').then((r) => r.data)
export const createProject = (data: Partial<Project>) => api.post<Project>('/projects', data).then((r) => r.data)
export const deleteProject = (id: string) => api.delete(`/projects/${id}`)

export const listEpisodes = (projectId: string) =>
  api.get<Episode[]>('/episodes', { params: { project_id: projectId } }).then((r) => r.data)
export const createEpisode = (projectId: string, data: { number: number; title: string }) =>
  api.post<Episode>(`/episodes?project_id=${projectId}`, data).then((r) => r.data)
export const deleteEpisode = (id: string) => api.delete(`/episodes/${id}`)
export const uploadVideo = (episodeId: string, file: File, onProgress?: (p: number) => void) => {
  const form = new FormData()
  form.append('file', file)
  return api.post<Episode>(`/episodes/${episodeId}/video`, form, {
    onUploadProgress: (e) => {
      if (e.total) onProgress?.(Math.round((e.loaded / e.total) * 100))
    },
  }).then((r) => r.data)
}

export const createJob = (episodeId: string, data: Record<string, any>) =>
  api.post<Job>(`/episodes/${episodeId}/jobs`, data).then((r) => r.data)
export const listJobs = (episodeId: string) =>
  api.get<Job[]>(`/episodes/${episodeId}/jobs`).then((r) => r.data)
export const listExports = (episodeId: string) =>
  api.get<{ name: string; size: number; url: string }[]>(`/episodes/${episodeId}/exports`).then((r) => r.data)
export const getJob = (jobId: string) => api.get<Job>(`/jobs/${jobId}`).then((r) => r.data)
export const retryJob = (jobId: string, stage: string) =>
  api.post<Job>(`/jobs/${jobId}/retry`, { stage }).then((r) => r.data)
export const cancelJob = (jobId: string) => api.post<Job>(`/jobs/${jobId}/cancel`).then((r) => r.data)

export const listSegments = (episodeId: string) =>
  api.get<Segment[]>(`/episodes/${episodeId}/segments`).then((r) => r.data)
export const updateSegment = (episodeId: string, segmentId: string, data: Partial<Segment>) =>
  api.put<Segment>(`/episodes/${episodeId}/segments/${segmentId}`, data).then((r) => r.data)

export const listTerms = (projectId?: string) =>
  api.get<GlossaryTerm[]>('/glossary', { params: projectId ? { project_id: projectId } : {} }).then((r) => r.data)
export const createTerm = (data: Partial<GlossaryTerm>) =>
  api.post<GlossaryTerm>('/glossary', data).then((r) => r.data)
export const updateTerm = (id: string, data: Partial<GlossaryTerm>) =>
  api.put<GlossaryTerm>(`/glossary/${id}`, data).then((r) => r.data)
export const deleteTerm = (id: string) => api.delete(`/glossary/${id}`)

export const listChannels = () => api.get<Channel[]>('/llm/channels').then((r) => r.data)
export const createChannel = (data: Record<string, any>) =>
  api.post<Channel>('/llm/channels', data).then((r) => r.data)
export const updateChannel = (id: string, data: Record<string, any>) =>
  api.put<Channel>(`/llm/channels/${id}`, data).then((r) => r.data)
export const deleteChannel = (id: string) => api.delete(`/llm/channels/${id}`)
export const testChannel = (id: string) =>
  api.post<{ ok: boolean; message: string; latency_ms: number; reply: string }>(
    `/llm/channels/${id}/test`,
  ).then((r) => r.data)

// ---------- 系统设置 ----------

export interface PathStatus {
  path: string
  exists: boolean
  is_dir: boolean
}

export interface AppSettings {
  data_dir: string
  database_url: string
  models_dir: string
  ffmpeg_path: string
  ffprobe_path: string
  ffmpeg_found: boolean
  ffprobe_found: boolean
  hf_endpoint: string
  asr_device: string
  asr_compute_type: string
  asr_beam_size: number
  derived: Record<string, PathStatus>
  restart_required_fields: string[]
}

export const getSettings = () => api.get<AppSettings>('/settings').then((r) => r.data)
export const updateSettings = (data: Partial<AppSettings>) =>
  api.put<AppSettings>('/settings', data).then((r) => r.data)

export function formatMs(ms: number | null | undefined): string {
  if (!ms && ms !== 0) return '-'
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}
