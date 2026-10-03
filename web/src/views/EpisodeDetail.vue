<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import {
  cancelJob, createJob, getJob, listChannels, listExports, listJobs, listSegments,
  retryJob, updateSegment, type Channel, type Episode, type Job, type Segment,
} from '../api'

const route = useRoute()
const episodeId = route.params.id as string

const job = ref<Job | null>(null)
const jobs = ref<Job[]>([])
const segments = ref<Segment[]>([])
const channels = ref<Channel[]>([])
const exports = ref<{ name: string; size: number; url: string }[]>([])
const episode = ref<Partial<Episode>>({})
const pollTimer = ref<number | null>(null)

const startDialog = ref(false)
const startForm = ref({ asr_model: 'large-v3', asr_language: 'ja', channel_id: '' })
const editingId = ref('')
const editingText = ref('')

const stageMeta: Record<string, { label: string; desc: string }> = {
  ingest: { label: '媒体接入', desc: 'FFmpeg 探测元信息、抽取 16k 单声道音轨' },
  asr: { label: '语音识别', desc: 'faster-whisper 逐行时间轴与原文' },
  translate: { label: 'LLM 翻译', desc: '分批翻译，术语注入' },
  subtitle: { label: '字幕生成', desc: '生成单语 / 双语 SRT' },
  export: { label: '交付', desc: '校验产物并登记下载' },
}

const stateTagType = (s: string) =>
  ({ succeeded: 'success', failed: 'danger', running: 'primary', queued: 'info', pending: 'info', canceled: 'warning', skipped: 'info' } as any)[s] ?? 'info'
const stateText = (s: string) =>
  ({ succeeded: '成功', failed: '失败', running: '运行中', queued: '排队中', pending: '等待', canceled: '已取消', skipped: '跳过' } as any)[s] ?? s
const dotText = (s: string) => (s === 'succeeded' ? '✓' : s === 'failed' ? '✕' : '')

const translatedCount = computed(() => segments.value.filter((s) => s.target_text.trim()).length)
const isRunning = computed(() => job.value && ['queued', 'running'].includes(job.value.state))

async function loadAll(silent = false) {
  try {
    const [jobsRes, segRes] = await Promise.all([
      listJobs(episodeId),
      segments.value.length || !silent ? listSegments(episodeId) : Promise.resolve(null),
    ])
    jobs.value = jobsRes
    job.value = jobsRes[0] ?? null
    if (segRes) segments.value = segRes
    if (job.value?.state === 'succeeded') {
      exports.value = await listExports(episodeId).catch(() => [])
    }
    if (isRunning.value && !pollTimer.value) schedulePoll()
  } catch (e: any) {
    if (!silent) ElMessage.error(e.message)
  }
}

function schedulePoll() {
  pollTimer.value = window.setTimeout(async () => {
    try {
      const fresh = await getJob(job.value!.id)
      job.value = fresh
      if (['queued', 'running'].includes(fresh.state)) {
        schedulePoll()
      } else {
        pollTimer.value = null
        await loadAll()
        ElMessage.success(flushMsg(fresh.state))
      }
    } catch {
      pollTimer.value = null
    }
  }, 1500)
}

function flushMsg(state: string) {
  if (state === 'succeeded') return '流水线执行完成，可下载字幕'
  if (state === 'canceled') return '任务已取消'
  return '流水线失败，请查看失败阶段的原因'
}

async function startJob() {
  try {
    await createJob(episodeId, {
      ...startForm.value,
      channel_id: startForm.value.channel_id || undefined,
    })
    startDialog.value = false
    ElMessage.success('任务已提交')
    await loadAll()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function retry(stage: string) {
  try {
    await retryJob(job.value!.id, stage)
    ElMessage.success(`已提交重试：${stageMeta[stage]?.label ?? stage}（该阶段及之后会重跑）`)
    loadAll()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function cancel() {
  await ElMessageBox.confirm('确定取消当前任务？', '取消任务', { type: 'warning' })
  try {
    await cancelJob(job.value!.id)
    loadAll()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

function beginEdit(row: Segment) {
  editingId.value = row.id
  editingText.value = row.target_text
}

async function saveEdit(row: Segment) {
  if (editingId.value !== row.id) return
  try {
    const fresh = await updateSegment(episodeId, row.id, { target_text: editingText.value })
    Object.assign(row, fresh)
    editingId.value = ''
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

function fmtMs(ms: number) {
  const t = Math.floor(ms / 1000)
  const h = String(Math.floor(t / 3600)).padStart(2, '0')
  const m = String(Math.floor((t % 3600) / 60)).padStart(2, '0')
  const s = String(t % 60).padStart(2, '0')
  return `${h}:${m}:${s}.${String(ms % 1000).padStart(3, '0')}`
}

function fmtSize(n: number) {
  return n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`
}

onMounted(async () => {
  await loadAll()
  try {
    channels.value = await listChannels()
  } catch { /* 忽略 */ }
})

onBeforeUnmount(() => {
  if (pollTimer.value) window.clearTimeout(pollTimer.value)
})
</script>

<template>
  <div>
    <PageHeader
      title="剧集工作台"
      description="启动流水线：媒体接入 → 语音识别 → LLM 翻译 → 字幕生成 → 交付；已成功阶段重跑时自动复用"
    >
      <el-button
        v-if="!isRunning" type="primary" size="large"
        @click="startDialog = true"
      >{{ job ? '重新运行流水线' : '启动流水线' }}</el-button>
      <el-button v-else type="warning" size="large" plain @click="cancel">取消任务</el-button>
    </PageHeader>

    <!-- 任务进度 -->
    <el-card v-if="job" shadow="never" class="block">
      <template #header>
        <div class="job-head">
          <span class="job-title">
            最近任务
            <el-tag :type="stateTagType(job.state)" size="small" round class="ml8">{{ stateText(job.state) }}</el-tag>
          </span>
          <span class="mono muted">{{ job.id.slice(0, 8) }}</span>
        </div>
      </template>

      <!-- 五阶段步骤条 -->
      <div class="pipe">
        <template v-for="(st, i) in job.stages" :key="st.id">
          <div class="pipe-node" :class="st.state">
            <div class="pipe-dot">{{ dotText(st.state) }}</div>
            <div class="pipe-label">{{ stageMeta[st.stage]?.label ?? st.stage }}</div>
            <div class="pipe-desc">{{ stageMeta[st.stage]?.desc }}</div>
            <div class="pipe-extra">
              <el-tag v-if="st.retry_count > 0" type="warning" size="small" round>重试 {{ st.retry_count }}</el-tag>
              <el-tooltip v-if="st.error" :content="st.error" placement="top" :show-after="200">
                <div class="pipe-err">{{ st.error }}</div>
              </el-tooltip>
              <el-button
                v-if="['failed', 'canceled'].includes(job.state) && ['failed', 'pending', 'skipped'].includes(st.state)"
                link type="primary" size="small" @click="retry(st.stage)"
              >重试此阶段</el-button>
            </div>
          </div>
          <div v-if="i < job.stages.length - 1" class="pipe-link"
            :class="{ done: st.state === 'succeeded' }" />
        </template>
      </div>

      <el-alert v-if="job.error" type="error" :title="job.error" :closable="false" show-icon class="mt16" />
      <div v-if="job.token_cost_json && Object.keys(job.token_cost_json).length" class="chips-row">
        <span class="chip">输入 {{ job.token_cost_json.prompt_tokens ?? 0 }} tokens</span>
        <span class="chip">输出 {{ job.token_cost_json.completion_tokens ?? 0 }} tokens</span>
        <span class="chip" v-if="job.token_cost_json.cost">≈ ${{ job.token_cost_json.cost }}</span>
      </div>
    </el-card>
    <el-alert
      v-else type="info" :closable="false" show-icon class="block"
      title="尚未启动任务：上传视频后点击「启动流水线」，字幕会自动生成"
    />

    <!-- 成品下载 -->
    <el-card v-if="exports.length" shadow="never" class="block">
      <template #header><b class="card-title">成品下载</b></template>
      <el-space wrap size="large">
        <el-button v-for="f in exports" :key="f.name" tag="a" :href="f.url" download type="primary" plain round>
          ↓ {{ f.name }}（{{ fmtSize(f.size) }}）
        </el-button>
      </el-space>
    </el-card>

    <!-- 字幕表 -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="job-head">
          <b class="card-title">字幕行（{{ segments.length }} 行 · 已译 {{ translatedCount }}）</b>
          <span class="muted" style="font-size: 12px">点击译文单元格可直接编辑，回车保存</span>
        </div>
      </template>
      <el-table :data="segments" stripe max-height="560">
        <el-table-column prop="index" label="#" width="52" />
        <el-table-column label="时间轴" width="225" class-name="mono">
          <template #default="{ row }">
            {{ fmtMs(row.start_ms) }} → {{ fmtMs(row.end_ms) }}
          </template>
        </el-table-column>
        <el-table-column prop="source_text" label="原文" min-width="280" show-overflow-tooltip />
        <el-table-column label="译文" min-width="300">
          <template #default="{ row }">
            <template v-if="editingId === row.id">
              <el-input v-model="editingText" size="small" autofocus
                @keyup.enter="saveEdit(row)" @blur="saveEdit(row)" />
            </template>
            <span v-else :class="{ placeholder: !row.target_text }" class="editable"
              @click="beginEdit(row)">
              {{ row.target_text || '（未翻译，点击填写）' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96" align="center">
          <template #default="{ row }">
            <el-tag size="small" round effect="light"
              :type="{ final: 'success', reviewed: 'success', translated: 'primary', draft: 'info' }[row.status] as any">
              {{ { final: '终稿', reviewed: '已复核', translated: '已翻译', draft: '草稿' }[row.status] }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="segments.length === 0" description="启动流水线并完成语音识别后，字幕行会出现在这里" />
    </el-card>

    <!-- 启动配置 -->
    <el-dialog v-model="startDialog" title="启动流水线" width="480">
      <el-form label-width="100">
        <el-form-item label="ASR 模型">
          <el-select v-model="startForm.asr_model" style="width: 100%">
            <el-option label="large-v3（最准 · 4G 卡实测 1.6GB 显存，推荐）" value="large-v3" />
            <el-option label="medium（更快 · 约 0.8GB 显存）" value="medium" />
            <el-option label="small（最快 · CPU 也可跑）" value="small" />
          </el-select>
        </el-form-item>
        <el-form-item label="源语言">
          <el-select v-model="startForm.asr_language" style="width: 100%">
            <el-option label="日语" value="ja" />
            <el-option label="英语" value="en" />
            <el-option label="韩语" value="ko" />
          </el-select>
        </el-form-item>
        <el-form-item label="翻译渠道">
          <el-select v-model="startForm.channel_id" placeholder="默认使用第一个启用的渠道" style="width: 100%" clearable>
            <el-option v-for="c in channels" :key="c.id" :label="`${c.name}（${c.model}）`" :value="c.id" />
          </el-select>
          <div class="muted" style="font-size: 12px; width: 100%">
            没有渠道？先到「翻译渠道」页添加 OpenAI 兼容渠道
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="startDialog = false">取消</el-button>
        <el-button type="primary" @click="startJob">启动</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.block { margin-bottom: 16px; }
.job-head { display: flex; justify-content: space-between; align-items: center; }
.job-title { font-weight: 600; }
.card-title { font-weight: 700; }
.ml8 { margin-left: 8px; }
.mt16 { margin-top: 16px; }
.muted { color: var(--at-muted); }
.chips-row { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.editable { cursor: text; min-height: 20px; display: inline-block; width: 100%; }
.placeholder { color: #c0c4cc; }
</style>
