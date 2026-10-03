<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import { createProject, deleteProject, listProjects, type Project } from '../api'

const router = useRouter()
const projects = ref<Project[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const form = ref({ title: '', description: '', source_lang: 'ja', target_lang: 'zh-Hans' })

const GRADS = 6
const gradOf = (p: Project) => {
  let h = 0
  for (const ch of p.id + p.title) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return `cover-grad-${h % GRADS}`
}
const glyphOf = (p: Project) => (p.title || '?').trim().charAt(0).toUpperCase()

async function load() {
  loading.value = true
  try {
    projects.value = await listProjects()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.value.title.trim()) return ElMessage.warning('请输入番剧名称')
  try {
    const p = await createProject(form.value)
    dialogVisible.value = false
    form.value = { title: '', description: '', source_lang: 'ja', target_lang: 'zh-Hans' }
    ElMessage.success('项目已创建')
    router.push(`/projects/${p.id}`)
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function remove(p: Project) {
  await ElMessageBox.confirm(`确定删除项目「${p.title}」？其下剧集、任务与字幕数据都会删除。`, '删除确认', { type: 'warning' })
  try {
    await deleteProject(p.id)
    ElMessage.success('已删除')
    load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <PageHeader title="番剧项目" description="创建番剧 → 添加剧集 → 上传生肉 → 一键出中文字幕">
      <el-button type="primary" size="large" @click="dialogVisible = true">＋ 新建项目</el-button>
    </PageHeader>

    <div v-if="projects.length" class="proj-grid">
      <div v-for="p in projects" :key="p.id" class="proj-card" @click="router.push(`/projects/${p.id}`)">
        <div class="proj-cover" :class="gradOf(p)">
          <span class="cover-glyph">{{ glyphOf(p) }}</span>
          <span class="cover-title">{{ p.title }}</span>
        </div>
        <div class="proj-body">
          <p class="proj-desc">{{ p.description || '暂无简介' }}</p>
          <div class="proj-meta">
            <el-tag size="small" effect="plain" round>{{ p.source_lang }} → {{ p.target_lang }}</el-tag>
            <span>{{ p.episode_count }} 集</span>
            <el-tag size="small" :type="p.status === 'active' ? 'success' : 'info'" effect="light" round>
              {{ p.status === 'active' ? '进行中' : '已归档' }}
            </el-tag>
          </div>
        </div>
        <div class="proj-foot" @click.stop>
          <el-button link type="primary" @click="router.push(`/projects/${p.id}`)">打开工作区</el-button>
          <el-button link type="danger" @click="remove(p)">删除</el-button>
        </div>
      </div>
    </div>

    <el-empty v-else-if="!loading" description="还没有项目">
      <el-button type="primary" @click="dialogVisible = true">＋ 新建项目</el-button>
    </el-empty>

    <el-dialog v-model="dialogVisible" title="新建番剧项目" width="480">
      <el-form label-width="90">
        <el-form-item label="番剧名称" required>
          <el-input v-model="form.title" placeholder="如：葬送的芙莉莲" maxlength="100" />
        </el-form-item>
        <el-form-item label="简介">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
        <el-form-item label="源语言">
          <el-select v-model="form.source_lang" style="width: 100%">
            <el-option label="日语 (ja)" value="ja" />
            <el-option label="英语 (en)" value="en" />
            <el-option label="韩语 (ko)" value="ko" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标语言">
          <el-select v-model="form.target_lang" style="width: 100%">
            <el-option label="简体中文 (zh-Hans)" value="zh-Hans" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>
