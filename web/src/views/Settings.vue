<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import {
  getSettings, updateSettings, type AppSettings, type PathStatus,
} from '../api'

const loading = ref(false)
const saving = ref(false)
const current = ref<AppSettings | null>(null)

const form = ref({
  data_dir: '',
  database_url: '',
  models_dir: '',
  ffmpeg_path: '',
  ffprobe_path: '',
  hf_endpoint: '',
  asr_device: 'auto',
  asr_compute_type: 'auto',
  asr_beam_size: 5,
})

const derivedRows = ref<{ name: string; status: PathStatus }[]>([])

async function load() {
  loading.value = true
  try {
    current.value = await getSettings()
    const c = current.value
    form.value = {
      data_dir: c.data_dir,
      database_url: c.database_url,
      models_dir: c.models_dir,
      ffmpeg_path: c.ffmpeg_found ? c.ffmpeg_path : '',
      ffprobe_path: c.ffprobe_found ? c.ffprobe_path : '',
      hf_endpoint: c.hf_endpoint,
      asr_device: c.asr_device,
      asr_compute_type: c.asr_compute_type,
      asr_beam_size: c.asr_beam_size,
    }
    derivedRows.value = Object.entries(c.derived).map(([name, status]) => ({ name, status }))
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const fresh = await updateSettings({ ...form.value })
    current.value = fresh
    derivedRows.value = Object.entries(fresh.derived).map(([name, status]) => ({ name, status }))
    const needRestart = ['data_dir', 'database_url', 'models_dir']
      .filter((f) => (form.value as any)[f] !== (fresh as any)[f])
    if (needRestart.length) {
      ElMessage({
        type: 'success',
        duration: 8000,
        message: `已保存。以下改动需要重启服务生效：${needRestart.join('、')}`,
      })
    } else {
      ElMessage.success('已保存并即时生效')
    }
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <PageHeader
      title="系统设置"
      description="本地路径与运行参数 —— 全部可配、不写死。保存到 runtime_config.json，优先级高于环境变量"
    >
      <el-button type="primary" size="large" :loading="saving" @click="save">保存设置</el-button>
    </PageHeader>

    <el-alert type="info" :closable="false" show-icon class="block"
      title="数据目录 / 数据库 / 模型目录的修改需重启服务后完全生效；FFmpeg 路径与 ASR 参数保存即生效。" />

    <el-card shadow="never" class="block">
      <template #header><b>路径配置</b></template>
      <el-form label-width="140">
        <el-form-item label="数据总目录">
          <el-input v-model="form.data_dir" />
          <div class="hint">媒体 / 任务产物 / 导出成品都在它下面（media / artifacts / exports 子目录）。改动后需把旧目录内容一并迁移，并重启服务。</div>
        </el-form-item>
        <el-form-item label="whisper 模型目录">
          <el-input v-model="form.models_dir" placeholder="留空 = 数据总目录/models" />
          <div class="hint">faster-whisper 模型缓存（改动后需重启；已有模型文件夹可整体迁移过来）</div>
        </el-form-item>
        <el-form-item label="数据库文件">
          <el-input v-model="form.database_url" />
          <div class="hint">SQLite：sqlite+aiosqlite:///G:/path/app.db（改动后需重启并迁移旧库文件）</div>
        </el-form-item>
        <el-form-item label="FFmpeg 路径">
          <el-input v-model="form.ffmpeg_path" :placeholder="`留空自动从 PATH 查找（当前：${current?.ffmpeg_path}）`" />
          <div class="hint" :class="{ bad: current && !current.ffmpeg_found }">
            {{ current?.ffmpeg_found ? '✓ 已检测到' : '✗ 未检测到，请填写完整路径' }}
          </div>
        </el-form-item>
        <el-form-item label="FFprobe 路径">
          <el-input v-model="form.ffprobe_path" :placeholder="`留空自动从 PATH 查找（当前：${current?.ffprobe_path}）`" />
          <div class="hint" :class="{ bad: current && !current.ffprobe_found }">
            {{ current?.ffprobe_found ? '✓ 已检测到' : '✗ 未检测到，请填写完整路径' }}
          </div>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header><b>派生目录（随数据总目录联动）</b></template>
      <el-table :data="derivedRows" size="small">
        <el-table-column prop="name" label="用途" min-width="160" />
        <el-table-column prop="status.path" label="路径" class-name="mono" min-width="300" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status.exists ? 'success' : 'danger'" size="small">
              {{ row.status.exists ? '存在' : '不存在' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header><b>语音识别（ASR）参数</b></template>
      <el-form label-width="140">
        <el-form-item label="设备">
          <el-select v-model="form.asr_device" style="width: 240px">
            <el-option label="auto（有 GPU 用 GPU）" value="auto" />
            <el-option label="cuda（强制 GPU）" value="cuda" />
            <el-option label="cpu" value="cpu" />
          </el-select>
        </el-form-item>
        <el-form-item label="量化类型">
          <el-select v-model="form.asr_compute_type" style="width: 240px">
            <el-option label="auto（GPU/CPU 均取 int8，4GB 显卡友好）" value="auto" />
            <el-option label="int8" value="int8" />
            <el-option label="int8_float16" value="int8_float16" />
            <el-option label="float16（仅新架构显卡）" value="float16" />
          </el-select>
        </el-form-item>
        <el-form-item label="Beam Size">
          <el-input-number v-model="form.asr_beam_size" :min="1" :max="8" />
          <span class="hint" style="margin-left: 12px">越大越准越慢；显存吃紧时设 1</span>
        </el-form-item>
        <el-form-item label="HF 镜像">
          <el-input v-model="form.hf_endpoint" placeholder="如 https://hf-mirror.com（模型下载加速）" />
          <div class="hint">新启动的进程生效</div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.page-head h2 { margin: 0 0 4px; font-size: 20px; }
.block { margin-bottom: 16px; }
.muted { color: #909399; font-size: 12px; }
.hint { color: #909399; font-size: 12px; line-height: 1.5; width: 100%; }
.hint.bad { color: var(--el-color-danger); }
</style>
