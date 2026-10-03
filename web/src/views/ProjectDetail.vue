<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import {
  createEpisode, deleteEpisode, formatMs, listEpisodes, type Episode, uploadVideo,
} from '../api'

const route = useRoute()
const router = useRouter()
const projectId = route.params.id as string

const episodes = ref<Episode[]>([])
const loading = ref(false)
const addDialog = ref(false)
const form = ref({ number: 1, title: '' })
const uploadingId = ref('')
const uploadPercent = ref(0)

const pctOf = (ep: Episode) =>
  ep.segment_count ? Math.round((ep.translated_count / ep.segment_count) * 100) : 0

async function load() {
  loading.value = true
  try {
    episodes.value = await listEpisodes(projectId)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function addEpisode() {
  try {
    await createEpisode(projectId, form.value)
    addDialog.value = false
    form.value = { number: episodes.value.length + 1, title: '' }
    ElMessage.success('剧集已添加，接下来上传视频')
    load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function customUpload(options: UploadRequestOptions) {
  const episodeId = (options.data as any)?.episodeId as string
  uploadingId.value = episodeId
  uploadPercent.value = 0
  try {
    await uploadVideo(episodeId, options.file, (p) => (uploadPercent.value = p))
    ElMessage.success('视频上传完成，元信息已解析')
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    uploadingId.value = ''
    load()
  }
}

async function remove(ep: Episode) {
  await ElMessageBox.confirm(`确定删除第 ${ep.number} 集？`, '删除确认', { type: 'warning' })
  try {
    await deleteEpisode(ep.id)
    load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <PageHeader title="剧集管理" description="每集对应一个生肉视频；上传后自动解析时长 / 分辨率 / 音轨">
      <el-button size="large" @click="router.push('/projects')">← 项目列表</el-button>
      <el-button type="primary" size="large" @click="addDialog = true; form.number = episodes.length + 1">
        ＋ 添加剧集
      </el-button>
    </PageHeader>

    <div v-if="episodes.length">
      <div v-for="ep in episodes" :key="ep.id" class="ep-card">
        <div class="ep-badge">
          <span class="no">{{ ep.number }}</span>
          <span class="cap">集</span>
        </div>

        <div class="ep-main">
          <div class="ep-title">
            {{ ep.title || '（未命名）' }}
            <el-tag v-if="ep.status === 'ready'" type="success" effect="light" size="small" round>就绪</el-tag>
            <el-tooltip v-else-if="ep.status === 'error'" :content="ep.error" placement="top">
              <el-tag type="danger" effect="light" size="small" round>探测失败</el-tag>
            </el-tooltip>
            <el-tag v-else type="info" effect="light" size="small" round>待上传</el-tag>
          </div>
          <div class="ep-file mono">{{ ep.video_name || '尚未上传视频' }}</div>
          <div class="ep-chips">
            <span class="chip" v-if="ep.duration_ms">⏱ {{ formatMs(ep.duration_ms) }}</span>
            <span class="chip" v-if="ep.width">{{ ep.width }}×{{ ep.height }}</span>
            <span class="chip">音轨 {{ ep.audio_tracks?.length ?? 0 }}</span>
            <span class="chip" :class="{ ok: ep.segment_count && ep.translated_count >= ep.segment_count }"
              v-if="ep.segment_count">字幕 {{ ep.translated_count }}/{{ ep.segment_count }}</span>
          </div>
          <el-progress
            v-if="ep.segment_count"
            :percentage="pctOf(ep)"
            :stroke-width="5"
            :show-text="false"
            style="max-width: 380px; margin-top: 8px"
          />
        </div>

        <div class="ep-actions">
          <el-upload
            v-if="!ep.video_path"
            :show-file-list="false"
            :http-request="customUpload"
            :data="{ episodeId: ep.id }"
            accept=".mp4,.mkv,.flv,.avi,.mov,.wmv,.webm,.ts"
          >
            <el-button type="primary" plain :loading="uploadingId === ep.id">
              {{ uploadingId === ep.id ? `上传中 ${uploadPercent}%` : '上传视频' }}
            </el-button>
          </el-upload>
          <el-button v-else type="primary" @click="router.push(`/episodes/${ep.id}`)">工作台</el-button>
          <el-button link type="danger" @click="remove(ep)">删除</el-button>
        </div>
      </div>
    </div>

    <el-empty v-else-if="!loading" description="还没有剧集，点击「添加剧集」开始">
      <el-button type="primary" @click="addDialog = true">＋ 添加剧集</el-button>
    </el-empty>

    <el-dialog v-model="addDialog" title="添加剧集" width="420">
      <el-form label-width="80">
        <el-form-item label="集数" required>
          <el-input-number v-model="form.number" :min="1" />
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="form.title" placeholder="如：旅立ちの車輪（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialog = false">取消</el-button>
        <el-button type="primary" @click="addEpisode">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>
