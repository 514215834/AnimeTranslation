<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import {
  createChannel, deleteChannel, listChannels, testChannel, updateChannel, type Channel,
} from '../api'

const channels = ref<Channel[]>([])
const loading = ref(false)
const testingId = ref('')
const dialogVisible = ref(false)
const editingId = ref('')
const form = ref({
  name: '', base_url: '', api_key: '', model: '',
  temperature: 0.3, rpm_limit: 0, price_in_per_m: 0, price_out_per_m: 0, enabled: true,
  extra_headers_text: '{}',
})

async function load() {
  loading.value = true
  try {
    channels.value = await listChannels()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  form.value = {
    name: '', base_url: '', api_key: '', model: '',
    temperature: 0.3, rpm_limit: 0, price_in_per_m: 0, price_out_per_m: 0, enabled: true,
    extra_headers_text: '{}',
  }
  dialogVisible.value = true
}

function openEdit(c: Channel) {
  editingId.value = c.id
  form.value = {
    name: c.name, base_url: c.base_url, api_key: '', model: c.model,
    temperature: c.temperature, rpm_limit: c.rpm_limit,
    price_in_per_m: c.price_in_per_m, price_out_per_m: c.price_out_per_m, enabled: c.enabled,
    extra_headers_text: JSON.stringify(c.extra_headers ?? {}, null, 0),
  }
  dialogVisible.value = true
}

async function submit() {
  if (!form.value.name || !form.value.base_url || !form.value.model)
    return ElMessage.warning('名称、base_url、模型必填')
  if (!editingId.value && !form.value.api_key)
    return ElMessage.warning('请填写 API Key')
  let extraHeaders: Record<string, string> = {}
  try {
    const parsed = JSON.parse(form.value.extra_headers_text || '{}')
    if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null)
      throw new Error('必须是 {\"头名\": \"值\"} 对象')
    extraHeaders = Object.fromEntries(Object.entries(parsed).map(([k, v]) => [k, String(v)]))
  } catch (e: any) {
    return ElMessage.warning(`自定义请求头不是合法 JSON：${e.message}`)
  }
  try {
    if (editingId.value) {
      const data: Record<string, any> = { ...form.value, extra_headers: extraHeaders }
      delete data.extra_headers_text
      if (!data.api_key) delete data.api_key // 空串=不改
      await updateChannel(editingId.value, data)
    } else {
      await createChannel({ ...form.value, extra_headers: extraHeaders, extra_headers_text: undefined })
    }
    dialogVisible.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function remove(c: Channel) {
  await ElMessageBox.confirm(`删除渠道「${c.name}」？`, '删除确认', { type: 'warning' })
  try {
    await deleteChannel(c.id)
    load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function test(c: Channel) {
  testingId.value = c.id
  try {
    const r = await testChannel(c.id)
    if (r.ok) ElMessage.success(`连通成功（${r.latency_ms}ms）：${r.reply}`)
    else ElMessage.error(r.message)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    testingId.value = ''
  }
}

async function toggle(c: Channel) {
  try {
    await updateChannel(c.id, { enabled: !c.enabled })
    c.enabled = !c.enabled
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader
      title="翻译渠道"
      description="任何 OpenAI 兼容接口均可接入：GPT / DeepSeek / Qwen / Gemini / Ollama / SakuraLLM 等；网关需要的特殊请求头在「自定义请求头」里配"
    >
      <el-button type="primary" size="large" @click="openCreate">＋ 添加渠道</el-button>
    </PageHeader>

    <el-table :data="channels" v-loading="loading" stripe>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="base_url" label="Base URL" min-width="220" class-name="mono" show-overflow-tooltip />
      <el-table-column prop="model" label="模型" min-width="160" class-name="mono" show-overflow-tooltip />
      <el-table-column prop="api_key_masked" label="API Key" width="170" class-name="mono" />
      <el-table-column label="费率（$/M）" width="130">
        <template #default="{ row }">{{ row.price_in_per_m }} / {{ row.price_out_per_m }}</template>
      </el-table-column>
      <el-table-column label="启用" width="80" align="center">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" size="small" @change="toggle(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="170" align="center">
        <template #default="{ row }">
          <el-button link type="primary" :loading="testingId === row.id" @click="test(row)">测试</el-button>
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && channels.length === 0"
      description="还没有翻译渠道。添加后才能运行流水线的翻译阶段" />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑渠道' : '添加渠道'" width="520">
      <el-form label-width="110">
        <el-form-item label="渠道名称" required>
          <el-input v-model="form.name" placeholder="如：DeepSeek 官方" />
        </el-form-item>
        <el-form-item label="Base URL" required>
          <el-input v-model="form.base_url" placeholder="如：https://api.deepseek.com/v1" />
        </el-form-item>
        <el-form-item label="模型" required>
          <el-input v-model="form.model" placeholder="如：deepseek-chat" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.api_key" type="password" show-password
            :placeholder="editingId ? '留空则不修改' : 'sk-...'" />
        </el-form-item>
        <el-form-item label="自定义请求头">
          <el-input v-model="form.extra_headers_text" type="textarea" :rows="2"
            placeholder='JSON 格式，如 {"x-opencode-session": "…"}（部分网关必需）' />
        </el-form-item>
        <el-form-item label="Temperature">
          <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" />
        </el-form-item>
        <el-form-item label="RPM 限制">
          <el-input-number v-model="form.rpm_limit" :min="0" :step="10" />
          <span class="muted ml8">0 = 不限</span>
        </el-form-item>
        <el-form-item label="费率 $/M">
          <el-input-number v-model="form.price_in_per_m" :min="0" :step="0.1" />
          <span class="muted" style="margin: 0 12px 0 4px">输入</span>
          <el-input-number v-model="form.price_out_per_m" :min="0" :step="0.1" />
          <span class="muted" style="margin-left: 4px">输出</span>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
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
.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.page-head h2 { margin: 0 0 4px; font-size: 20px; }
.muted { color: #909399; font-size: 12px; }
.ml8 { margin-left: 8px; }
</style>
