<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  AlertCircle, Check, CheckCircle2, Clock3, Database, KeyRound, Activity,
  Pencil, Plus, RefreshCw, Server, ShieldCheck, ToggleLeft, X,
} from 'lucide-vue-next'
import { apiRequest } from '../lib/api'

type ProviderType = 'openai_compatible' | 'openai' | 'deepseek' | 'qwen' | 'anthropic'
type EmbeddingProviderType = 'openai_compatible' | 'openai' | 'qwen' | 'deepseek'

interface PlatformModel {
  id: string
  code: string
  display_name: string
  provider_type: ProviderType
  upstream_model_name: string
  base_url: string
  credential_ref: string | null
  enabled: boolean
  created_at: string
}
interface ModelPrice {
  id: string
  platform_model_id: string
  revision: number
  currency: string
  input_per_million_minor: string | number
  output_per_million_minor: string | number
  effective_from: string
  effective_until: string | null
  created_at: string
}
interface EmbeddingModel {
  id: string
  code: string
  display_name: string
  provider_type: EmbeddingProviderType
  upstream_model_name: string
  base_url: string
  credential_ref: string
  dimensions: number
  enabled: boolean
  created_at: string
}

const activeTab = ref<'chat' | 'embedding'>('chat')
const models = ref<PlatformModel[]>([])
const embeddingModels = ref<EmbeddingModel[]>([])
const prices = ref<ModelPrice[]>([])
const selectedModelId = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const modelModal = ref(false)
const priceModal = ref(false)
const embeddingModal = ref(false)
const editingModelId = ref<string | null>(null)
const editingEmbeddingId = ref<string | null>(null)
const testingEmbeddingId = ref<string | null>(null)

const modelForm = ref({
  code: '',
  display_name: '',
  provider_type: 'deepseek' as ProviderType,
  upstream_model_name: '',
  base_url: 'https://api.deepseek.com',
  credential_ref: 'deepseek-primary',
  enabled: false,
})
const priceForm = ref({ currency: 'CNY', input_yuan: '', output_yuan: '' })
const embeddingForm = ref({
  code: '',
  display_name: '',
  provider_type: 'openai_compatible' as EmbeddingProviderType,
  upstream_model_name: '',
  base_url: '',
  credential_ref: '',
  dimensions: 1536,
  enabled: false,
})

const selectedModel = computed(() => models.value.find(item => item.id === selectedModelId.value) ?? null)
const enabledModels = computed(() => models.value.filter(item => item.enabled).length)
const enabledEmbeddings = computed(() => embeddingModels.value.filter(item => item.enabled).length)

function providerLabel(value: string): string {
  const labels: Record<string, string> = {
    openai_compatible: 'OpenAI 兼容',
    openai: 'OpenAI',
    deepseek: 'DeepSeek',
    qwen: '通义千问',
    anthropic: 'Anthropic',
  }
  return labels[value] ?? value
}
function notify(message: string) {
  notice.value = message
  window.setTimeout(() => {
    if (notice.value === message) notice.value = ''
  }, 3200)
}
function errorMessage(value: unknown): string {
  return value instanceof Error ? value.message : '操作失败，请稍后重试'
}
async function loadPrices(modelId: string) {
  if (!modelId) {
    prices.value = []
    return
  }
  prices.value = await apiRequest<ModelPrice[]>(`/admin/platform/models/${modelId}/prices`)
}
async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    const [modelData, embeddingData] = await Promise.all([
      apiRequest<PlatformModel[]>('/admin/platform/models'),
      apiRequest<EmbeddingModel[]>('/admin/platform/embedding-models'),
    ])
    models.value = modelData
    embeddingModels.value = embeddingData
    if (!models.value.some(item => item.id === selectedModelId.value)) {
      selectedModelId.value = models.value[0]?.id ?? ''
    }
    await loadPrices(selectedModelId.value)
  } catch (value) {
    error.value = errorMessage(value)
  } finally {
    loading.value = false
  }
}
function editModel(model?: PlatformModel) {
  editingModelId.value = model?.id ?? null
  modelForm.value = model
    ? {
        code: model.code,
        display_name: model.display_name,
        provider_type: model.provider_type,
        upstream_model_name: model.upstream_model_name,
        base_url: model.base_url,
        credential_ref: model.credential_ref ?? '',
        enabled: model.enabled,
      }
    : {
        code: '',
        display_name: '',
        provider_type: 'deepseek',
        upstream_model_name: '',
        base_url: 'https://api.deepseek.com',
        credential_ref: 'deepseek-primary',
        enabled: false,
      }
  modelModal.value = true
}
function providerPresetChanged() {
  const presets: Record<ProviderType, string> = {
    openai_compatible: '',
    openai: 'https://api.openai.com/v1',
    deepseek: 'https://api.deepseek.com',
    qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    anthropic: 'https://api.anthropic.com/v1',
  }
  modelForm.value.base_url = presets[modelForm.value.provider_type]
}
async function saveModel() {
  const form = modelForm.value
  if (!form.code.trim() || !form.display_name.trim() || !form.upstream_model_name.trim() ||
      !form.base_url.trim() || !form.credential_ref.trim()) {
    notify('请完整填写模型编码、展示名称、上游模型、Base URL 和凭据引用')
    return
  }
  const payload = {
    ...form,
    code: form.code.trim(),
    display_name: form.display_name.trim(),
    upstream_model_name: form.upstream_model_name.trim(),
    base_url: form.base_url.trim(),
    credential_ref: form.credential_ref.trim(),
  }
  saving.value = true
  try {
    if (editingModelId.value) {
      await apiRequest<PlatformModel>(`/admin/platform/models/${editingModelId.value}`, {
        method: 'PATCH',
        body: JSON.stringify({
          display_name: payload.display_name,
          provider_type: payload.provider_type,
          upstream_model_name: payload.upstream_model_name,
          base_url: payload.base_url,
          credential_ref: payload.credential_ref,
          enabled: payload.enabled,
        }),
      })
      notify('平台模型已更新')
    } else {
      await apiRequest<PlatformModel>('/admin/platform/models', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      notify('平台模型已创建')
    }
    modelModal.value = false
    await loadAll()
  } catch (value) {
    notify(errorMessage(value))
  } finally {
    saving.value = false
  }
}
async function toggleModel(model: PlatformModel) {
  try {
    await apiRequest<PlatformModel>(`/admin/platform/models/${model.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: !model.enabled }),
    })
    await loadAll()
    notify(model.enabled ? '平台模型已停用' : '平台模型已启用')
  } catch (value) {
    notify(errorMessage(value))
  }
}
function addPrice() {
  if (!selectedModelId.value) return
  priceForm.value = { currency: 'CNY', input_yuan: '', output_yuan: '' }
  priceModal.value = true
}
async function savePrice() {
  const input = Number(priceForm.value.input_yuan)
  const output = Number(priceForm.value.output_yuan)
  if (!Number.isFinite(input) || input < 0 || !Number.isFinite(output) || output < 0) {
    notify('请输入有效的模型价格')
    return
  }
  saving.value = true
  try {
    await apiRequest<ModelPrice>(`/admin/platform/models/${selectedModelId.value}/prices`, {
      method: 'POST',
      body: JSON.stringify({
        currency: priceForm.value.currency,
        input_per_million_minor: (input * 100).toFixed(6),
        output_per_million_minor: (output * 100).toFixed(6),
        effective_from: new Date().toISOString(),
        effective_until: null,
      }),
    })
    priceModal.value = false
    await loadPrices(selectedModelId.value)
    await loadAll()
    notify('模型价格版本已发布')
  } catch (value) {
    notify(errorMessage(value))
  } finally {
    saving.value = false
  }
}
function editEmbedding(model?: EmbeddingModel) {
  editingEmbeddingId.value = model?.id ?? null
  embeddingForm.value = model
    ? {
        code: model.code,
        display_name: model.display_name,
        provider_type: model.provider_type,
        upstream_model_name: model.upstream_model_name,
        base_url: model.base_url,
        credential_ref: model.credential_ref,
        dimensions: 1536,
        enabled: model.enabled,
      }
    : {
        code: '',
        display_name: '',
        provider_type: 'openai_compatible',
        upstream_model_name: '',
        base_url: '',
        credential_ref: '',
        dimensions: 1536,
        enabled: false,
      }
  embeddingModal.value = true
}
function embeddingPresetChanged() {
  const presets: Record<EmbeddingProviderType, string> = {
    openai_compatible: '',
    openai: 'https://api.openai.com/v1',
    qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    deepseek: '',
  }
  embeddingForm.value.base_url = presets[embeddingForm.value.provider_type]
}
async function testEmbedding(model: EmbeddingModel) {
  testingEmbeddingId.value = model.id
  try {
    const result = await apiRequest<{ success: boolean; dimensions: number; expected_dimensions: number; input_tokens: number | null }>(
      '/admin/platform/embedding-models/' + model.id + '/test', { method: 'POST' },
    )
    notify('连接成功：返回 ' + result.dimensions + ' 维向量，符合知识库 1536 维要求')
  } catch (value) {
    notify(errorMessage(value))
  } finally {
    testingEmbeddingId.value = null
  }
}
async function saveEmbedding() {
  const form = embeddingForm.value
  if (!form.code.trim() || !form.display_name.trim() || !form.upstream_model_name.trim() ||
      !form.base_url.trim() || !form.credential_ref.trim()) {
    notify('请完整填写 Embedding 模型信息')
    return
  }
  const payload = {
    ...form,
    code: form.code.trim(),
    display_name: form.display_name.trim(),
    upstream_model_name: form.upstream_model_name.trim(),
    base_url: form.base_url.trim(),
    credential_ref: form.credential_ref.trim(),
    dimensions: 1536,
  }
  saving.value = true
  try {
    if (editingEmbeddingId.value) {
      await apiRequest<EmbeddingModel>(`/admin/platform/embedding-models/${editingEmbeddingId.value}`, {
        method: 'PATCH',
        body: JSON.stringify({
          display_name: payload.display_name,
          provider_type: payload.provider_type,
          upstream_model_name: payload.upstream_model_name,
          base_url: payload.base_url,
          credential_ref: payload.credential_ref,
          dimensions: 1536,
          enabled: payload.enabled,
        }),
      })
      notify('平台 Embedding 模型已更新')
    } else {
      await apiRequest<EmbeddingModel>('/admin/platform/embedding-models', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      notify('平台 Embedding 模型已创建')
    }
    embeddingModal.value = false
    await loadAll()
  } catch (value) {
    notify(errorMessage(value))
  } finally {
    saving.value = false
  }
}
async function toggleEmbedding(model: EmbeddingModel) {
  try {
    await apiRequest<EmbeddingModel>(`/admin/platform/embedding-models/${model.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: !model.enabled }),
    })
    await loadAll()
    notify(model.enabled ? 'Embedding 模型已停用' : 'Embedding 模型已启用')
  } catch (value) {
    notify(errorMessage(value))
  }
}
function formatMinor(value: string | number, currency: string): string {
  const amount = Number(value) / 100
  try {
    return new Intl.NumberFormat('zh-CN', { style: 'currency', currency }).format(amount)
  } catch {
    return `${currency} ${amount.toFixed(2)}`
  }
}
function formatDate(value: string): string {
  return new Date(value).toLocaleString()
}
onMounted(() => { void loadAll() })
</script>

<template>
  <section class="platform-admin">
    <div class="admin-intro">
      <div>
        <small>PLATFORM ADMINISTRATION</small>
        <h2>平台模型与供应商</h2>
        <p>对话模型与 Embedding 模型独立管理。Embedding 使用 OpenAI 兼容接口，必须通过真实调用验证 1536 维输出。</p>
      </div>
      <button class="admin-button secondary" :disabled="loading" @click="loadAll"><RefreshCw :size="15"/>刷新</button>
    </div>

    <div class="admin-security">
      <ShieldCheck :size="18"/>
      <div>
        <b>平台凭据安全说明</b>
        <p>这里仅填写凭据引用，不填写真实 API Key。API 与 Knowledge Worker 都必须配置 SUPERNOVA_PLATFORM_CREDENTIALS_JSON；Embedding 模型保存后应先测试连接及实际向量维度，再启用供租户选择。</p>
      </div>
    </div>

    <div v-if="error" class="admin-error"><AlertCircle :size="17"/>{{error}}</div>
    <div v-if="notice" class="admin-notice"><CheckCircle2 :size="16"/>{{notice}}</div>

    <div class="admin-tabs">
      <button :class="{active:activeTab==='chat'}" @click="activeTab='chat'"><Server :size="15"/>对话模型池</button>
      <button :class="{active:activeTab==='embedding'}" @click="activeTab='embedding'"><Database :size="15"/>Embedding 模型池</button>
    </div>

    <template v-if="activeTab==='chat'">
      <section class="admin-panel">
        <div class="admin-panel-head">
          <div><b>平台对话模型</b><small>已配置 {{models.length}} 个，启用 {{enabledModels}} 个</small></div>
          <button class="admin-button primary" @click="editModel()"><Plus :size="15"/>添加平台模型</button>
        </div>
        <div v-if="loading" class="admin-empty">正在读取平台模型…</div>
        <div v-else-if="!models.length" class="admin-empty">
          <Server :size="28"/><b>尚未配置平台模型</b>
          <span>先配置服务器凭据，再添加模型、价格并启用。</span>
        </div>
        <div v-else class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>模型名称</th><th>供应商</th><th>上游模型</th><th>凭据引用</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="model in models" :key="model.id" :class="{selected:model.id===selectedModelId}" @click="selectedModelId=model.id;loadPrices(model.id)">
                <td><b>{{model.display_name}}</b><small>{{model.code}}</small></td>
                <td>{{providerLabel(model.provider_type)}}</td>
                <td><code>{{model.upstream_model_name}}</code><small>{{model.base_url}}</small></td>
                <td><code>{{model.credential_ref||'未设置'}}</code></td>
                <td><span :class="model.enabled?'status-on':'status-off'">{{model.enabled?'已启用':'已停用'}}</span></td>
                <td><div class="admin-actions"><button @click.stop="editModel(model)" title="编辑"><Pencil :size="14"/></button><button @click.stop="toggleModel(model)" :title="model.enabled?'停用':'启用'"><ToggleLeft :size="15"/></button></div></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="selectedModel" class="admin-panel">
        <div class="admin-panel-head">
          <div><b>{{selectedModel.display_name}} · 价格版本</b><small>价格按版本追加，历史价格不会被覆盖。</small></div>
          <button class="admin-button secondary" @click="addPrice"><Plus :size="15"/>新增价格版本</button>
        </div>
        <div v-if="prices.length" class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>版本</th><th>币种</th><th>输入价格 / 百万 Token</th><th>输出价格 / 百万 Token</th><th>生效时间</th><th>结束时间</th></tr></thead>
            <tbody><tr v-for="price in prices" :key="price.id"><td>v{{price.revision}}</td><td>{{price.currency}}</td><td>{{formatMinor(price.input_per_million_minor,price.currency)}}</td><td>{{formatMinor(price.output_per_million_minor,price.currency)}}</td><td>{{formatDate(price.effective_from)}}</td><td>{{price.effective_until?formatDate(price.effective_until):'当前版本'}}</td></tr></tbody>
          </table>
        </div>
        <div v-else class="admin-empty compact"><Clock3 :size="22"/><span>该模型还没有价格版本。发布当前生效价格后，租户才能看到并选择它。</span></div>
      </section>
    </template>

    <template v-else>
      <section class="admin-panel">
        <div class="admin-panel-head">
          <div><b>平台 Embedding 模型</b><small>已配置 {{embeddingModels.length}} 个，启用 {{enabledEmbeddings}} 个；当前向量维度固定为 1536。</small></div>
          <button class="admin-button primary" @click="editEmbedding()"><Plus :size="15"/>添加 Embedding 模型</button>
        </div>
        <div v-if="loading" class="admin-empty">正在读取 Embedding 模型…</div>
        <div v-else-if="!embeddingModels.length" class="admin-empty">
          <Database :size="28"/><b>尚未配置平台 Embedding 模型</b>
          <span>知识库使用平台 Embedding 前，需要先添加可用的向量模型和服务器凭据。</span>
        </div>
        <div v-else class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>模型名称</th><th>供应商</th><th>上游模型</th><th>维度</th><th>凭据引用</th><th>状态</th><th>操作</th></tr></thead>
            <tbody><tr v-for="model in embeddingModels" :key="model.id">
              <td><b>{{model.display_name}}</b><small>{{model.code}}</small></td><td>{{providerLabel(model.provider_type)}}</td><td><code>{{model.upstream_model_name}}</code><small>{{model.base_url}}</small></td><td>{{model.dimensions}}</td><td><code>{{model.credential_ref}}</code></td><td><span :class="model.enabled?'status-on':'status-off'">{{model.enabled?'已启用':'已停用'}}</span></td>
              <td><div class="admin-actions"><button :disabled="testingEmbeddingId===model.id" @click="testEmbedding(model)" title="测试连接和 1536 维输出"><Activity :size="14"/></button><button @click="editEmbedding(model)" title="编辑"><Pencil :size="14"/></button><button @click="toggleEmbedding(model)" :title="model.enabled?'停用':'启用'"><ToggleLeft :size="15"/></button></div></td>
            </tr></tbody>
          </table>
        </div>
      </section>
    </template>

    <div class="admin-footnote"><KeyRound :size="15"/>真实 API Key 不会从服务器返回。credential_ref 必须与 SUPERNOVA_PLATFORM_CREDENTIALS_JSON 中的键名完全一致。</div>

    <div v-if="modelModal" class="admin-backdrop" @click.self="modelModal=false">
      <section class="admin-modal">
        <header><div><small>CHAT MODEL</small><h3>{{editingModelId?'编辑平台模型':'添加平台模型'}}</h3></div><button @click="modelModal=false"><X :size="18"/></button></header>
        <div class="admin-form">
          <label>模型编码 <input v-model="modelForm.code" :disabled="!!editingModelId" maxlength="80" placeholder="例如 deepseek-chat"></label>
          <label>展示名称 <input v-model="modelForm.display_name" maxlength="100" placeholder="例如 DeepSeek Chat"></label>
          <label>供应商类型 <select v-model="modelForm.provider_type" @change="providerPresetChanged"><option value="qwen">通义千问</option><option value="openai">OpenAI</option><option value="openai_compatible">OpenAI 兼容</option><option value="anthropic">Anthropic</option></select></label>
          <label>上游模型名称 <input v-model="modelForm.upstream_model_name" maxlength="120" placeholder="供应商侧的真实模型 ID"></label>
          <label class="wide">Base URL <input v-model="modelForm.base_url" maxlength="2048" placeholder="https://api.example.com/v1"></label>
          <label class="wide">凭据引用 <input v-model="modelForm.credential_ref" maxlength="255" placeholder="例如 deepseek-primary"><small>必须与服务器环境变量 JSON 的键名一致。</small></label>
          <label class="admin-switch"><span>创建后启用模型</span><input v-model="modelForm.enabled" type="checkbox"></label>
        </div>
        <footer><button class="admin-button secondary" @click="modelModal=false">取消</button><button class="admin-button primary" :disabled="saving" @click="saveModel"><Check :size="15"/>保存模型</button></footer>
      </section>
    </div>

    <div v-if="priceModal" class="admin-backdrop" @click.self="priceModal=false">
      <section class="admin-modal narrow">
        <header><div><small>PRICE REVISION</small><h3>发布价格版本</h3></div><button @click="priceModal=false"><X :size="18"/></button></header>
        <p class="admin-modal-hint">价格以元/百万 Token 填写；系统会转换为最小货币单位并保留历史版本。</p>
        <div class="admin-form">
          <label>计价币种 <select v-model="priceForm.currency"><option value="CNY">人民币 CNY</option><option value="USD">美元 USD</option></select></label>
          <label>输入价格（元 / 百万 Token）<input v-model="priceForm.input_yuan" type="number" min="0" step="0.000001" placeholder="例如 2.00"></label>
          <label class="wide">输出价格（元 / 百万 Token）<input v-model="priceForm.output_yuan" type="number" min="0" step="0.000001" placeholder="例如 8.00"></label>
        </div>
        <footer><button class="admin-button secondary" @click="priceModal=false">取消</button><button class="admin-button primary" :disabled="saving" @click="savePrice"><Check :size="15"/>发布价格</button></footer>
      </section>
    </div>

    <div v-if="embeddingModal" class="admin-backdrop" @click.self="embeddingModal=false">
      <section class="admin-modal">
        <header><div><small>EMBEDDING MODEL</small><h3>{{editingEmbeddingId?'编辑 Embedding 模型':'添加 Embedding 模型'}}</h3></div><button @click="embeddingModal=false"><X :size="18"/></button></header>
        <div class="admin-form">
          <label>模型编码 <input v-model="embeddingForm.code" :disabled="!!editingEmbeddingId" maxlength="80" placeholder="例如 qwen-embedding-v3"></label>
          <label>展示名称 <input v-model="embeddingForm.display_name" maxlength="100" placeholder="例如 Qwen Embedding V3"></label>
          <label>供应商类型 <select v-model="embeddingForm.provider_type" @change="embeddingPresetChanged"><option value="openai_compatible">OpenAI 兼容</option><option value="openai">OpenAI</option><option value="qwen">通义千问</option><option value="deepseek">DeepSeek</option></select></label>
          <label>上游 Embedding 模型 ID <input v-model="embeddingForm.upstream_model_name" maxlength="120" placeholder="例如服务商提供的 embedding 模型 ID"><small>必须是 Embedding 模型，不能填写 DeepSeek-chat 等对话模型 ID。</small></label>
          <label class="wide">OpenAI 兼容 Base URL <input v-model="embeddingForm.base_url" maxlength="2048" placeholder="https://api.provider.cn/v1"><small>填写服务商提供 Embeddings API 的根地址；系统会请求 /embeddings。仅支持 HTTPS 公网服务。</small></label>
          <label class="wide">凭据引用 <input v-model="embeddingForm.credential_ref" maxlength="255" placeholder="例如 siliconflow-embedding-primary"><small>需在 API 和 Knowledge Worker 的 SUPERNOVA_PLATFORM_CREDENTIALS_JSON 中配置同名键。</small></label>
          <label>向量维度 <input value="1536（固定）" disabled><small>服务商实际返回维度必须为 1536；保存后请执行连接测试。</small></label>
          <label class="admin-switch"><span>创建后启用模型</span><input v-model="embeddingForm.enabled" type="checkbox"></label>
        </div>
        <footer><button class="admin-button secondary" @click="embeddingModal=false">取消</button><button class="admin-button primary" :disabled="saving" @click="saveEmbedding"><Check :size="15"/>保存模型</button></footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.platform-admin{display:flex;flex-direction:column;gap:15px;color:#334155}
.admin-intro{display:flex;align-items:flex-end;justify-content:space-between;gap:18px}
.admin-intro small,.admin-modal header small{font-size:9px;letter-spacing:1.5px;color:#8ea1bd;font-weight:700}
.admin-intro h2{font-size:18px;margin:7px 0 5px;color:#26364e}
.admin-intro p{font-size:11px;color:#8794a7;margin:0;line-height:1.7}
.admin-security,.admin-footnote{display:flex;align-items:flex-start;gap:10px;padding:13px 15px;border:1px solid #e4edfc;background:#f4f8ff;border-radius:10px;color:#6481ae}
.admin-security>svg,.admin-footnote>svg{flex-shrink:0;margin-top:1px}
.admin-security b{font-size:11px;color:#496b9d}
.admin-security p{font-size:10px;line-height:1.7;margin:4px 0 0;color:#7187a9}
.admin-footnote{align-items:center;font-size:10px;line-height:1.6}
.admin-error,.admin-notice{display:flex;align-items:center;gap:8px;border-radius:9px;padding:11px 13px;font-size:11px}
.admin-error{background:#fff2f1;color:#c45e58;border:1px solid #ffddda}
.admin-notice{background:#effaf5;color:#269a73;border:1px solid #d8f2e6}
.admin-tabs{display:flex;gap:7px;background:#f1f4f9;padding:4px;border-radius:9px;width:max-content}
.admin-tabs button{display:flex;align-items:center;gap:7px;border:0;background:transparent;border-radius:7px;padding:9px 13px;font-size:11px;color:#7d8aa0}
.admin-tabs button.active{background:white;color:#3479ed;box-shadow:0 1px 4px #26364e14}
.admin-panel{background:white;border:1px solid #e9edf4;border-radius:12px;padding:17px 18px}
.admin-panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.admin-panel-head>div{display:flex;flex-direction:column;gap:5px}
.admin-panel-head b{font-size:13px;color:#2f3f58}
.admin-panel-head small{font-size:10px;color:#9aa5b5}
.admin-button{height:34px;border-radius:8px;padding:0 11px;display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid transparent;font-size:10px;font-weight:600;white-space:nowrap}
.admin-button:disabled{opacity:.55;cursor:not-allowed}
.admin-button.primary{background:#397cf6;color:white}
.admin-button.secondary{background:white;border-color:#e5eaf2;color:#62728a}
.admin-table-wrap{overflow-x:auto}
.admin-table{width:100%;border-collapse:collapse;text-align:left;white-space:nowrap;font-size:10px}
.admin-table th{background:#fafbfd;color:#929eaf;font-size:9px;font-weight:600;padding:10px;border-bottom:1px solid #edf0f5}
.admin-table td{padding:11px 10px;border-bottom:1px solid #f0f2f6;color:#637188;vertical-align:middle}
.admin-table tr:last-child td{border-bottom:0}
.admin-table tbody tr{cursor:pointer}
.admin-table tbody tr.selected{background:#f7faff}
.admin-table td b,.admin-table td small{display:block}
.admin-table td b{font-size:10px;color:#33445d;margin-bottom:4px}
.admin-table td small{font-size:9px;color:#9aa5b5;margin-top:4px;max-width:250px;overflow:hidden;text-overflow:ellipsis}
.admin-table code{font-size:9px;color:#57729b}
.status-on,.status-off{display:inline-block;padding:4px 8px;border-radius:20px;font-size:9px}
.status-on{background:#eaf9f3;color:#1c9d77}
.status-off{background:#f1f3f6;color:#8994a5}
.admin-actions{display:flex;gap:3px}
.admin-actions button{width:28px;height:28px;display:grid;place-items:center;border:0;background:transparent;color:#8794a7;border-radius:6px}
.admin-actions button:hover{background:#edf4ff;color:#397cf6}
.admin-empty{min-height:135px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:8px;color:#9aa5b5;font-size:10px;text-align:center}
.admin-empty b{font-size:12px;color:#53647e}
.admin-empty span{max-width:420px;line-height:1.7}
.admin-empty.compact{min-height:75px;flex-direction:row}
.admin-modal-hint{font-size:10px;color:#8794a7;line-height:1.7;margin:-7px 0 15px}
.admin-backdrop{position:fixed;inset:0;z-index:60;background:#17243b70;backdrop-filter:blur(3px);display:grid;place-items:center;padding:18px}
.admin-modal{width:min(650px,100%);max-height:90vh;overflow:auto;background:white;border-radius:15px;padding:21px;box-shadow:0 22px 70px #16284933}
.admin-modal.narrow{width:min(490px,100%)}
.admin-modal header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px}
.admin-modal header h3{font-size:18px;color:#293951;margin:6px 0 0}
.admin-modal header button{border:0;background:transparent;color:#8b97a8}
.admin-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}
.admin-form label{display:flex;flex-direction:column;gap:6px;font-size:10px;color:#75839a;min-width:0}
.admin-form label.wide{grid-column:1/-1}
.admin-form input,.admin-form select{height:37px;width:100%;box-sizing:border-box;border:1px solid #e7ecf3;border-radius:7px;padding:0 10px;font-size:11px;color:#43536b;background:#fbfcfe;outline:none}
.admin-form input:focus,.admin-form select:focus{border-color:#8db5fa;background:white}
.admin-form label small{font-size:9px;color:#9aa5b5;line-height:1.5}
.admin-form .admin-switch{flex-direction:row;align-items:center;justify-content:space-between;border:1px solid #edf0f5;border-radius:8px;padding:9px 10px}
.admin-form .admin-switch input{width:16px;height:16px}
.admin-modal footer{display:flex;justify-content:flex-end;gap:8px;margin-top:19px}
@media(max-width:760px){.admin-intro{align-items:flex-start}.admin-panel-head{align-items:flex-start;flex-direction:column}.admin-form{grid-template-columns:1fr}.admin-form label.wide{grid-column:auto}.admin-tabs{width:100%}.admin-tabs button{flex:1;justify-content:center}.admin-modal{padding:16px}}
</style>
