<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import {
  createTerm, deleteTerm, listProjects, listTerms, updateTerm,
  type GlossaryTerm, type Project,
} from '../api'

const terms = ref<GlossaryTerm[]>([])
const projects = ref<Project[]>([])
const projectId = ref('')
const keyword = ref('')
const loading = ref(false)
const dialogVisible = ref(false)
const editingId = ref('')
const form = ref({ source: '', target: '', aliases: '', note: '', locked: false, project_id: '' })

async function load() {
  loading.value = true
  try {
    terms.value = await listTerms(projectId.value || undefined)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  form.value = { source: '', target: '', aliases: '', note: '', locked: false, project_id: projectId.value }
  dialogVisible.value = true
}

function openEdit(t: GlossaryTerm) {
  editingId.value = t.id
  form.value = {
    source: t.source, target: t.target, aliases: (t.aliases || []).join('，'),
    note: t.note, locked: t.locked, project_id: t.project_id ?? '',
  }
  dialogVisible.value = true
}

async function submit() {
  if (!form.value.source.trim() || !form.value.target.trim())
    return ElMessage.warning('原文与译文必填')
  const payload = {
    source: form.value.source.trim(),
    target: form.value.target.trim(),
    aliases: form.value.aliases.split(/[，,、\s]+/).map((s) => s.trim()).filter(Boolean),
    note: form.value.note,
    locked: form.value.locked,
    project_id: form.value.project_id || null,
  }
  try {
    if (editingId.value) await updateTerm(editingId.value, payload)
    else await createTerm(payload)
    dialogVisible.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function remove(t: GlossaryTerm) {
  await ElMessageBox.confirm(`删除词条「${t.source} → ${t.target}」？`, '删除确认', { type: 'warning' })
  try {
    await deleteTerm(t.id)
    load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function toggleLock(t: GlossaryTerm) {
  try {
    await updateTerm(t.id, { locked: !t.locked })
    t.locked = !t.locked
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

onMounted(async () => {
  load()
  try {
    projects.value = await listProjects()
  } catch { /* ignore */ }
})
</script>

<template>
  <div>
    <PageHeader title="全局术语表" description="命中术语的行翻译时强制使用这里的译法（人名、招式名、组织名），锁定词条重跑不漂移">
      <div class="filters">
        <el-select v-model="projectId" placeholder="全部项目" clearable style="width: 190px" @change="load">
          <el-option v-for="p in projects" :key="p.id" :label="p.title" :value="p.id" />
        </el-select>
        <el-input v-model="keyword" placeholder="搜索原文/译文" style="width: 190px" @keyup.enter="load" />
        <el-button type="primary" @click="openCreate">＋ 新增词条</el-button>
      </div>
    </PageHeader>

    <el-table :data="terms" v-loading="loading" stripe>
      <el-table-column prop="source" label="原文" min-width="180" show-overflow-tooltip />
      <el-table-column prop="target" label="译文" min-width="160" show-overflow-tooltip />
      <el-table-column label="别名" min-width="160">
        <template #default="{ row }">{{ (row.aliases || []).join('、') || '-' }}</template>
      </el-table-column>
      <el-table-column prop="note" label="备注" min-width="160" show-overflow-tooltip />
      <el-table-column label="范围" width="120">
        <template #default="{ row }">
          <el-tag v-if="!row.project_id" type="warning" size="small">全局</el-tag>
          <span v-else class="muted">{{ projects.find((p) => p.id === row.project_id)?.title ?? '项目词条' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="锁定" width="90" align="center">
        <template #default="{ row }">
          <el-switch :model-value="row.locked" size="small" @change="toggleLock(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" align="center">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && terms.length === 0"
      description="术语表为空。命中术语的行翻译时会强制使用这里的译法（如人名、招式名）" />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑词条' : '新增词条'" width="460">
      <el-form label-width="70">
        <el-form-item label="原文" required>
          <el-input v-model="form.source" placeholder="如：フリーレン" />
        </el-form-item>
        <el-form-item label="译文" required>
          <el-input v-model="form.target" placeholder="如：芙莉莲" />
        </el-form-item>
        <el-form-item label="别名">
          <el-input v-model="form.aliases" placeholder="逗号分隔，同样参与匹配" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="归属">
          <el-select v-model="form.project_id" style="width: 100%">
            <el-option label="全局（所有项目生效）" value="" />
            <el-option v-for="p in projects" :key="p.id" :label="`项目：${p.title}`" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="锁定">
          <el-switch v-model="form.locked" />
          <span class="muted ml8">锁定后重跑翻译也不漂移</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-head h2 { margin: 0; font-size: 20px; }
.filters { display: flex; gap: 8px; }
.muted { color: #909399; font-size: 12px; }
.ml8 { margin-left: 8px; }
</style>
