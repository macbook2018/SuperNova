<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  Activity, AlertCircle, ArrowRight, ArrowUpRight, Bell, Bot, Check, CheckCircle2,
  ChevronDown, ChevronRight, CircleHelp, Clock3, Copy, Cpu, Database, Download,
  Eye, EyeOff, FileText, Filter, Gauge, KeyRound, LayoutDashboard, LifeBuoy,
  LogOut, Mail, Menu, MessageSquare, MoreHorizontal, Pencil, Plus, Search, Server,
  Settings2, ShieldCheck, Sparkles, Trash2, Upload, UserRound, Wallet, X, Zap,
} from 'lucide-vue-next'
import {
  API_BASE_URL,
  apiRequest,
  clearStoredSession,
  getStoredSession,
  storeSession,
  type ApiKey,
  type ApiKeyCreated,
  type AuthResponse,
  type KnowledgeBase,
  type ModelProvider,
  type Persona,
  type Tenant,
  type User,
} from './lib/api'

type Page = 'overview' | 'personas' | 'providers' | 'knowledge' | 'knowledgeDetail' | 'api' | 'usage' | 'settings'
type PersonaView = Persona & { color: string }
type KnowledgeBaseView = KnowledgeBase & { color: string; docs: never[] }

const page = ref<Page>('overview')
const mobileOpen = ref(false)
const query = ref('')
const toast = ref('')
let toastTimer: number | undefined
const authenticated = ref(false)
const currentUser = ref<User | null>(null)
const currentTenant = ref<Tenant | null>(null)
const authMode = ref<'login' | 'register'>('login')
const authBusy = ref(false)
const authError = ref('')
const authForm = ref({ email: '', password: '', display_name: '', workspace_name: '' })

const titles: Record<Page, string> = {
  overview: '工作台总览',
  personas: '人格管理',
  providers: '模型供应商',
  knowledge: '知识库',
  knowledgeDetail: '知识库详情',
  api: 'API 接口',
  usage: '用量与账单',
  settings: '账户设置',
}
const descriptions: Record<Page, string> = {
  overview: '查看工作空间中的人格、模型供应商和资源状态。',
  personas: '创建和管理独立 AI 人格，为每个人格配置模型与行为方式。',
  providers: '集中管理模型供应商、兼容接口地址和加密保存的 API Key。',
  knowledge: '集中管理知识库资料空间，并关联到一个或多个 AI 人格。',
  knowledgeDetail: '管理知识库基本信息及其关联人格。',
  api: '管理用户级 API Key；统一对话网关将在下一阶段开放。',
  usage: 'Token 计量和账单将在统一 API 网关阶段接入。',
  settings: '查看账户资料与安全设置。',
}
const nav = [
  {
    label: '工作空间',
    items: [
      { key: 'overview' as Page, label: '工作台总览', icon: LayoutDashboard },
      { key: 'personas' as Page, label: '人格管理', icon: Bot },
      { key: 'knowledge' as Page, label: '知识库', icon: Database },
    ],
  },
  {
    label: '模型配置',
    items: [{ key: 'providers' as Page, label: '模型供应商', icon: Server }],
  },
  {
    label: '开发者',
    items: [
      { key: 'api' as Page, label: 'API 接口', icon: KeyRound },
      { key: 'usage' as Page, label: '用量与账单', icon: Activity },
    ],
  },
]
const activeNav = computed(() => page.value === 'knowledgeDetail' ? 'knowledge' : page.value)

const personas = ref<PersonaView[]>([])
const providers = ref<ModelProvider[]>([])
const kbs = ref<KnowledgeBaseView[]>([])
const apiKeys = ref<ApiKey[]>([])
const selectedId = ref('')
const selectedKb = computed<KnowledgeBaseView>(() =>
  kbs.value.find(k => k.id === selectedId.value) ??
  kbs.value[0] ??
  { id: '', name: '', description: '', documents_count: 0, created_at: '', updated_at: '', color: 'blue', docs: [] },
)
const linkedPersonas = computed(() =>
  personas.value.filter(persona => persona.knowledge_base_ids.includes(selectedKb.value.id)),
)
const totalDocs = computed(() => kbs.value.reduce((count, kb) => count + kb.documents_count, 0))
const filteredPersonas = computed(() => {
  const term = query.value.trim().toLowerCase()
  return term
    ? personas.value.filter(persona =>
        [persona.name, persona.slug, persona.description].some(value => value.toLowerCase().includes(term)),
      )
    : personas.value
})
const personaRatio = computed(() =>
  personas.value.length ? personas.value.filter(persona => persona.enabled).length / personas.value.length * 100 : 0,
)

function notify(message: string) {
  toast.value = message
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 2600)
}
async function copy(value: string, message = '已复制到剪贴板') {
  try {
    await navigator.clipboard.writeText(value)
    notify(message)
  } catch {
    notify('复制失败，请手动选择文本')
  }
}
function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body !== undefined) init.body = JSON.stringify(body)
  return apiRequest<T>(path, init)
}
function colorFor(index: number): string {
  return ['blue', 'purple', 'green'][index % 3]
}

async function loadWorkspace() {
  const [personaData, providerData, knowledgeData, keyData] = await Promise.all([
    request<Persona[]>('/personas'),
    request<ModelProvider[]>('/model-providers'),
    request<KnowledgeBase[]>('/knowledge-bases'),
    request<ApiKey[]>('/api-keys'),
  ])
  personas.value = personaData.map((persona, index) => ({ ...persona, color: colorFor(index) }))
  providers.value = providerData
  kbs.value = knowledgeData.map((kb, index) => ({ ...kb, color: colorFor(index), docs: [] }))
  apiKeys.value = keyData
  if (!kbs.value.some(kb => kb.id === selectedId.value)) selectedId.value = kbs.value[0]?.id ?? ''
}
async function loadIdentity() {
  const identity = await request<{ user: User; tenant: Tenant }>('/auth/me')
  currentUser.value = identity.user
  currentTenant.value = identity.tenant
  authenticated.value = true
  await loadWorkspace()
}
onMounted(async () => {
  if (!getStoredSession()) return
  try {
    await loadIdentity()
  } catch (error) {
    clearStoredSession()
    authenticated.value = false
    authError.value = error instanceof Error ? error.message : '登录状态已失效，请重新登录'
  }
})
async function submitAuth() {
  authBusy.value = true
  authError.value = ''
  try {
    const path = authMode.value === 'login' ? '/auth/login' : '/auth/register'
    const payload = authMode.value === 'login'
      ? { email: authForm.value.email, password: authForm.value.password }
      : {
          email: authForm.value.email,
          password: authForm.value.password,
          display_name: authForm.value.display_name,
          workspace_name: authForm.value.workspace_name || undefined,
        }
    const response = await request<AuthResponse>(path, 'POST', payload)
    storeSession(response)
    currentUser.value = response.user
    currentTenant.value = response.tenant
    authenticated.value = true
    await loadWorkspace()
    notify(authMode.value === 'login' ? '登录成功' : '账户创建成功')
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '请求失败，请稍后重试'
  } finally {
    authBusy.value = false
  }
}
async function logout() {
  const session = getStoredSession()
  try {
    if (session?.refresh_token) {
      await request('/auth/logout', 'POST', { refresh_token: session.refresh_token })
    }
  } catch {
    // Local credentials are cleared even if the network logout fails.
  }
  clearStoredSession()
  authenticated.value = false
  currentUser.value = null
  currentTenant.value = null
  personas.value = []
  providers.value = []
  kbs.value = []
  apiKeys.value = []
  page.value = 'overview'
  notify('已退出登录')
}
function go(next: Page) {
  page.value = next
  mobileOpen.value = false
  query.value = ''
}

const personaModal = ref(false)
const editingId = ref<string | null>(null)
const personaForm = ref({
  name: '',
  slug: '',
  description: '',
  system_prompt: '',
  provider_id: '',
  model_name: '',
  temperature: 0.7,
  top_p: 1,
  max_tokens: 2048,
  knowledge_base_ids: [] as string[],
  enabled: true,
})
function editPersona(persona?: PersonaView) {
  editingId.value = persona?.id ?? null
  personaForm.value = persona
    ? {
        name: persona.name,
        slug: persona.slug,
        description: persona.description,
        system_prompt: persona.system_prompt,
        provider_id: persona.provider_id ?? '',
        model_name: persona.model_name,
        temperature: persona.temperature,
        top_p: persona.top_p,
        max_tokens: persona.max_tokens,
        knowledge_base_ids: [...persona.knowledge_base_ids],
        enabled: persona.enabled,
      }
    : {
        name: '',
        slug: '',
        description: '',
        system_prompt: '',
        provider_id: providers.value[0]?.id ?? '',
        model_name: '',
        temperature: 0.7,
        top_p: 1,
        max_tokens: 2048,
        knowledge_base_ids: [],
        enabled: true,
      }
  personaModal.value = true
}
async function savePersona() {
  if (!personaForm.value.name.trim() || !personaForm.value.slug.trim() ||
      !personaForm.value.system_prompt.trim() || !personaForm.value.model_name.trim()) {
    notify('请填写人格名称、调用标识、模型名称和系统提示词')
    return
  }
  const payload = {
    ...personaForm.value,
    provider_id: personaForm.value.provider_id || null,
  }
  try {
    if (editingId.value) {
      await request<Persona>(`/personas/${editingId.value}`, 'PATCH', payload)
      notify('人格配置已保存')
    } else {
      await request<Persona>('/personas', 'POST', payload)
      notify('人格创建成功')
    }
    personaModal.value = false
    await loadWorkspace()
  } catch (error) {
    notify(error instanceof Error ? error.message : '保存人格失败')
  }
}
async function togglePersona(persona: PersonaView) {
  try {
    await request<Persona>(`/personas/${persona.id}`, 'PATCH', { enabled: !persona.enabled })
    await loadWorkspace()
    notify(persona.enabled ? '人格已停用' : '人格已启用')
  } catch (error) {
    notify(error instanceof Error ? error.message : '更新人格状态失败')
  }
}
async function deletePersona(persona: PersonaView) {
  if (!window.confirm(`确定删除人格「${persona.name}」吗？`)) return
  try {
    await request<void>(`/personas/${persona.id}`, 'DELETE')
    await loadWorkspace()
    notify('人格已删除')
  } catch (error) {
    notify(error instanceof Error ? error.message : '删除人格失败')
  }
}

const providerModal = ref(false)
const editingProviderId = ref<string | null>(null)
const providerForm = ref({
  name: '',
  provider_type: 'deepseek' as ModelProvider['provider_type'],
  base_url: 'https://api.deepseek.com',
  api_key: '',
  enabled: true,
})
function providerPresetChanged() {
  const presets: Record<ModelProvider['provider_type'], string> = {
    deepseek: 'https://api.deepseek.com',
    openai: 'https://api.openai.com/v1',
    qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    anthropic: 'https://api.anthropic.com/v1',
    openai_compatible: '',
  }
  providerForm.value.base_url = presets[providerForm.value.provider_type]
}
function editProvider(provider?: ModelProvider) {
  editingProviderId.value = provider?.id ?? null
  providerForm.value = provider
    ? {
        name: provider.name,
        provider_type: provider.provider_type,
        base_url: provider.base_url,
        api_key: '',
        enabled: provider.enabled,
      }
    : {
        name: '',
        provider_type: 'deepseek',
        base_url: 'https://api.deepseek.com',
        api_key: '',
        enabled: true,
      }
  providerModal.value = true
}
async function saveProvider() {
  if (!providerForm.value.name.trim() || !providerForm.value.base_url.trim()) {
    notify('请填写供应商名称和 Base URL')
    return
  }
  if (!editingProviderId.value && !providerForm.value.api_key.trim()) {
    notify('新建供应商时必须填写 API Key')
    return
  }
  const payload: Record<string, unknown> = {
    name: providerForm.value.name,
    provider_type: providerForm.value.provider_type,
    base_url: providerForm.value.base_url,
    enabled: providerForm.value.enabled,
  }
  if (providerForm.value.api_key.trim()) payload.api_key = providerForm.value.api_key.trim()
  try {
    if (editingProviderId.value) {
      await request<ModelProvider>(`/model-providers/${editingProviderId.value}`, 'PATCH', payload)
      notify('模型供应商已更新')
    } else {
      await request<ModelProvider>('/model-providers', 'POST', payload)
      notify('模型供应商创建成功')
    }
    providerModal.value = false
    await loadWorkspace()
  } catch (error) {
    notify(error instanceof Error ? error.message : '保存模型供应商失败')
  }
}
async function toggleProvider(provider: ModelProvider) {
  try {
    await request<ModelProvider>(`/model-providers/${provider.id}`, 'PATCH', { enabled: !provider.enabled })
    await loadWorkspace()
    notify(provider.enabled ? '供应商已停用' : '供应商已启用')
  } catch (error) {
    notify(error instanceof Error ? error.message : '更新供应商状态失败')
  }
}
async function deleteProvider(provider: ModelProvider) {
  if (!window.confirm(`确定删除模型供应商「${provider.name}」吗？`)) return
  try {
    await request<void>(`/model-providers/${provider.id}`, 'DELETE')
    await loadWorkspace()
    notify('模型供应商已删除')
  } catch (error) {
    notify(error instanceof Error ? error.message : '删除失败；请检查是否仍有人格正在使用')
  }
}

const kbModal = ref(false)
const kbForm = ref({ name: '', description: '' })
async function createKb() {
  if (!kbForm.value.name.trim()) {
    notify('请填写知识库名称')
    return
  }
  try {
    const created = await request<KnowledgeBase>('/knowledge-bases', 'POST', kbForm.value)
    kbForm.value = { name: '', description: '' }
    kbModal.value = false
    await loadWorkspace()
    selectedId.value = created.id
    page.value = 'knowledgeDetail'
    notify('知识库创建成功')
  } catch (error) {
    notify(error instanceof Error ? error.message : '创建知识库失败')
  }
}
function openKb(kb: KnowledgeBaseView) {
  selectedId.value = kb.id
  page.value = 'knowledgeDetail'
}
async function deleteKb(kb: KnowledgeBaseView) {
  if (!window.confirm(`确定删除知识库「${kb.name}」吗？关联关系也会一并移除。`)) return
  try {
    await request<void>(`/knowledge-bases/${kb.id}`, 'DELETE')
    await loadWorkspace()
    page.value = 'knowledge'
    notify('知识库已删除')
  } catch (error) {
    notify(error instanceof Error ? error.message : '删除知识库失败')
  }
}
function upload() {
  notify('知识库文档上传、解析和向量化将在后续知识库阶段接入；当前已完成知识库元数据与人格关联')
}

const keyModal = ref(false)
const secretModal = ref(false)
const newKeyName = ref('我的应用')
const newApiKeySecret = ref('')
const baseUrl = `${API_BASE_URL}/v1`
const example = `curl ${baseUrl}/chat/completions \\
  -H "Authorization: Bearer YOUR_USER_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "customer-support",
    "messages": [{"role": "user", "content": "你好"}]
  }'`
async function createApiKey() {
  if (!newKeyName.value.trim()) return notify('请填写密钥名称')
  try {
    const result = await request<ApiKeyCreated>('/api-keys', 'POST', { name: newKeyName.value.trim() })
    newApiKeySecret.value = result.secret
    keyModal.value = false
    secretModal.value = true
    await loadWorkspace()
  } catch (error) {
    notify(error instanceof Error ? error.message : '创建 API Key 失败')
  }
}
async function rotateKey(key: ApiKey) {
  if (!window.confirm(`轮换「${key.name}」后，旧密钥将立即失效。确定继续吗？`)) return
  try {
    const result = await request<ApiKeyCreated>(`/api-keys/${key.id}/rotate`, 'POST')
    newApiKeySecret.value = result.secret
    secretModal.value = true
    await loadWorkspace()
  } catch (error) {
    notify(error instanceof Error ? error.message : '轮换 API Key 失败')
  }
}
async function revokeKey(key: ApiKey) {
  if (!window.confirm(`确定撤销 API Key「${key.name}」吗？`)) return
  try {
    await request<void>(`/api-keys/${key.id}`, 'DELETE')
    await loadWorkspace()
    notify('API Key 已撤销')
  } catch (error) {
    notify(error instanceof Error ? error.message : '撤销 API Key 失败')
  }
}

const period = ref('日')
const labels = computed(() =>
  period.value === '日'
    ? ['08', '09', '10', '11', '12', '13', '14', '15', '16', '17', '18', '19']
    : period.value === '周'
      ? ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
      : ['06 月', '07 月', '08 月', '09 月', '10 月', '11 月'],
)
const bars = computed(() =>
  period.value === '日'
    ? [28, 42, 36, 58, 47, 72, 54, 66, 48, 83, 62, 76]
    : period.value === '周'
      ? [36, 52, 44, 68, 57, 83, 72]
      : [42, 56, 48, 74, 62, 86],
)
</script>

<template>
<section v-if="!authenticated" class="auth-shell">
 <div class="auth-card">
  <div class="auth-brand"><img src="/logo.svg" alt="SuperNova"><div><b>SuperNova</b><small>智能体服务平台</small></div></div>
  <div class="auth-intro"><span>WELCOME TO SUPERNOVA</span><h1>{{authMode==='login'?'欢迎回来':'创建你的工作空间'}}</h1><p>{{authMode==='login'?'登录后管理你的人格、模型供应商和知识库。':'注册账户后即可创建独立的 AI 人格与模型配置。'}}</p></div>
  <div class="auth-tabs"><button :class="{active:authMode==='login'}" @click="authMode='login';authError=''">登录</button><button :class="{active:authMode==='register'}" @click="authMode='register';authError=''">注册账户</button></div>
  <form class="auth-form" @submit.prevent="submitAuth">
   <label><span>邮箱地址</span><input v-model="authForm.email" type="email" autocomplete="email" placeholder="name@example.com" required></label>
   <label><span>登录密码</span><input v-model="authForm.password" type="password" :autocomplete="authMode==='login'?'current-password':'new-password'" minlength="12" maxlength="128" placeholder="至少 12 位字符" required></label>
   <label v-if="authMode==='register'"><span>账户名称</span><input v-model="authForm.display_name" autocomplete="name" maxlength="80" placeholder="你的名称" required></label>
   <label v-if="authMode==='register'"><span>工作空间名称（选填）</span><input v-model="authForm.workspace_name" maxlength="100" placeholder="默认使用账户名称创建"></label>
   <p v-if="authError" class="auth-error">{{authError}}</p>
   <button class="btn primary auth-submit" type="submit" :disabled="authBusy">{{authBusy?'请稍候…':authMode==='login'?'登录 SuperNova':'创建账户'}}</button>
  </form>
  <p class="auth-footnote">账户数据按工作空间隔离。请勿在不可信设备上保存登录状态。</p>
 </div>
</section>
<div v-else class="app-shell">
 <div v-if="mobileOpen" class="scrim" @click="mobileOpen=false"></div>
 <aside class="sidebar" :class="{'sidebar-open':mobileOpen}">
  <div class="brand"><img src="/logo.svg" alt="SuperNova"><div><b>SuperNova</b><small>智能体平台</small></div><i>BETA</i></div>
  <button class="workspace" @click="notify('当前账户使用单一默认工作空间')"><span>{{(currentTenant?.name||'S').slice(0,1).toUpperCase()}}</span><div><b>{{currentTenant?.name||'我的工作空间'}}</b><small>{{currentTenant?.role==='owner'?'工作空间所有者':'工作空间成员'}}</small></div><ChevronDown :size="15"/></button>
  <nav><div v-for="g in nav" :key="g.label" class="nav-group"><small>{{g.label}}</small><button v-for="n in g.items" :key="n.key" :class="{active:activeNav===n.key}" @click="go(n.key)"><component :is="n.icon" :size="18"/>{{n.label}}<em v-if="n.key==='personas'">{{personas.length}}</em></button></div></nav>
  <div class="sidebar-bottom"><div class="help"><LifeBuoy :size="17"/><div><b>需要帮助？</b><small>查看使用指南与接口文档</small></div></div><button class="side-setting" @click="go('settings')"><Settings2 :size="17"/>账户设置</button><div class="profile"><span>{{(currentUser?.display_name||'U').slice(0,1).toUpperCase()}}</span><div><b>{{currentUser?.display_name||'用户'}}</b><small>{{currentUser?.email}}</small></div><button @click="logout" aria-label="退出登录"><LogOut :size="16"/></button></div></div>
 </aside>
 <main class="main">
  <header><div class="crumb"><button class="menu-btn" @click="mobileOpen=true"><Menu :size="20"/></button><span>SuperNova</span><ChevronRight :size="14"/><b>{{titles[page]}}</b></div><div class="top-actions"><span class="demo"><i></i>API 已连接</span><button @click="notify('帮助中心将在后续版本接入')"><CircleHelp :size="18"/></button><button @click="notify('暂时没有新通知')"><Bell :size="18"/></button><span class="top-avatar">{{(currentUser?.display_name||'U').slice(0,1).toUpperCase()}}</span></div></header>
  <div class="content">
   <div class="heading"><div><small>SUPERNOVA CONSOLE</small><h1>{{titles[page]}}</h1><p>{{descriptions[page]}}</p></div><div class="heading-actions">
    <button v-if="page==='overview'||page==='personas'" class="btn primary" @click="editPersona()"><Plus :size="16"/>新建人格</button>
    <button v-else-if="page==='providers'" class="btn primary" @click="editProvider()"><Plus :size="16"/>添加供应商</button>
    <button v-else-if="page==='knowledge'" class="btn primary" @click="kbModal=true"><Plus :size="16"/>新建知识库</button>
    <template v-else-if="page==='knowledgeDetail'">
    <section class="panel kb-detail"><span class="kb-icon" :class="selectedKb.color"><Database :size="23"/></span><div><h2>{{selectedKb.name}}</h2><p>{{selectedKb.description}}</p><small>{{selectedKb.documents_count}} 份文档 · 文档处理待接入</small></div><button class="btn secondary" @click="deleteKb(selectedKb)"><Trash2 :size="15"/>删除知识库</button></section>
    <section class="panel"><div class="panel-title"><div><b>文档管理</b><small>文档上传、解析和向量化检索将在后续知识库阶段实现。</small></div><button class="btn secondary small" @click="upload"><Upload :size="14"/>上传文档</button></div><div class="empty"><FileText :size="28"/><b>当前知识库还没有文档</b><small>本阶段已完成知识库元数据保存；文档处理流水线将在后续阶段接入。</small></div></section>
    <section class="panel binding"><div class="panel-title"><div><b>已关联人格</b><small>一个知识库可以关联多个 AI 人格；在人格编辑窗口中管理关联。</small></div><a @click="go('personas')">管理人格 →</a></div><span v-for="persona in linkedPersonas" :key="persona.id"><Bot :size="14"/> {{persona.name}} <Check :size="13"/></span><div v-if="!linkedPersonas.length" class="empty"><b>尚未关联人格</b><small>编辑人格并选择此知识库即可建立关联。</small></div></section>
   </template>

   <template v-else-if="page==='api'">
    <section class="api-notice"><ShieldCheck :size="21"/><div><b>一个用户级密钥，未来调用多个独立人格</b><p>密钥已接入真实账户与数据库。OpenAI 兼容对话网关将在 Phase 4 开放，当前尚不能使用此地址发起模型请求。</p></div></section>
    <section class="api-grid"><div class="api-main">
     <article class="panel"><div class="panel-title"><div><b>我的 API Key</b><small>密钥明文仅在创建或轮换时展示一次。</small></div><button class="btn primary small" @click="keyModal=true"><Plus :size="14"/>创建密钥</button></div>
      <div v-if="apiKeys.length" class="key-list">
       <div v-for="key in apiKeys" :key="key.id" class="key-item"><div><b>{{key.name}}</b><small><code>{{key.key_prefix}}••••</code> · {{new Date(key.created_at).toLocaleString()}}</small><label :class="key.status==='active'?'running':'paused'">{{key.status==='active'?'有效':key.status==='revoked'?'已撤销':'已过期'}}</label></div><div class="row-actions"><button @click="rotateKey(key)" :disabled="key.status!=='active'" aria-label="轮换密钥"><Zap :size="15"/></button><button @click="revokeKey(key)" :disabled="key.status!=='active'" aria-label="撤销密钥"><Trash2 :size="15"/></button></div></div>
      </div>
      <div v-else class="empty"><KeyRound :size="28"/><b>还没有 API Key</b><small>创建一个用户级密钥，后续可用于调用该工作空间中的任意人格。</small><button class="btn primary" @click="keyModal=true"><Plus :size="15"/>创建 API Key</button></div>
     </article>
     <article class="panel"><div class="panel-title"><div><b>接口连接信息</b><small>统一对话网关将在 Phase 4 提供 OpenAI 兼容接口。</small></div><label class="paused">待开放</label></div><div class="endpoint"><div><small>预定 Base URL</small><code>{{baseUrl}}</code></div><button @click="copy(baseUrl,'Base URL 已复制')"><Copy :size="15"/></button></div><div class="endpoint"><div><small>预定 API 路径</small><code>/chat/completions</code></div><button @click="copy('/chat/completions')"><Copy :size="15"/></button></div><div class="endpoint"><div><small>认证方式</small><code>Authorization: Bearer YOUR_USER_API_KEY</code></div><button @click="copy('Authorization: Bearer YOUR_USER_API_KEY')"><Copy :size="15"/></button></div><p class="warning"><AlertCircle :size="14"/>当前只完成 API Key 生命周期管理；真实请求路由、流式响应和 Token 计量尚未上线。</p></article>
     <article class="panel"><div class="panel-title"><div><b>调用示例（预览）</b><small>Phase 4 上线后可使用此请求结构。</small></div><a @click="copy(example,'示例代码已复制')"><Copy :size="14"/>复制代码</a></div><pre>{{example}}</pre></article>
    </div><aside class="api-side"><article class="panel"><div class="panel-title"><div><b>已启用的人格</b><small>可在统一网关上线后通过 model 选择</small></div></div><div v-for="persona in personas.filter(p=>p.enabled)" :key="persona.id" class="model-row"><span class="persona-icon" :class="persona.color"><Bot :size="15"/></span><div><b>{{persona.name}}</b><small>{{persona.slug}}</small></div><i></i></div><div v-if="!personas.some(p=>p.enabled)" class="empty"><small>暂无已启用的人格</small></div><a @click="go('personas')">管理人格 →</a></article><article class="panel tips"><LifeBuoy :size="18"/><b>密钥安全提示</b><ul><li>不要将 API Key 提交到代码仓库。</li><li>生产环境使用 HTTPS 和独立密钥管理。</li><li>轮换后旧密钥立即失效。</li><li>密钥明文无法再次查看，请妥善保存。</li></ul></article></aside></section>
   </template>

   <template v-else-if="page==='usage'">
    <section class="panel empty"><Activity :size="30"/><b>真实用量计量尚未接入</b><small>统一 API 网关将在 Phase 4 记录输入 Token、输出 Token、模型调用次数和响应时间。</small><button class="btn secondary" @click="go('api')">查看 API 接入规划</button></section>
   </template>

   <template v-else-if="page==='settings'">
    <section class="panel settings"><div class="panel-title"><div><b>基本资料</b><small>管理你的账户展示信息</small></div></div><div class="account"><span>{{(currentUser?.display_name||'U').slice(0,1).toUpperCase()}}</span><div><b>{{currentUser?.display_name}}</b><small>{{currentTenant?.name}} · {{currentTenant?.role}}</small></div></div><div class="settings-fields"><label>账户名称<input :value="currentUser?.display_name||''" readonly></label><label>登录邮箱<input :value="currentUser?.email||''" readonly></label><label>账户类型<input value="工作空间账户" readonly></label></div></section>
    <section class="panel settings"><div class="panel-title"><div><b>安全与隐私</b><small>保护你的账户和 API 资源</small></div></div><div class="setting-row"><ShieldCheck :size="18"/><div><b>登录密码</b><small>定期更新密码，保护账户安全</small></div><button class="btn secondary small" @click="notify('密码修改接口将在后续账户安全阶段接入')">修改密码</button></div><div class="setting-row"><KeyRound :size="18"/><div><b>API 密钥</b><small>管理第三方应用的访问凭证</small></div><a @click="go('api')">前往管理 →</a></div></section>
    <section class="panel settings"><div class="panel-title"><div><b>通知偏好</b><small>选择你希望接收的系统通知</small></div></div><div class="setting-row"><Bell :size="18"/><div><b>用量提醒</b><small>当 Token 额度即将耗尽时提醒我</small></div><span class="toggle on"></span></div><div class="setting-row"><MessageSquare :size="18"/><div><b>系统公告</b><small>接收产品更新和维护通知</small></div><span class="toggle"></span></div></section>
   </template>
   <footer class="page-footer"><span>© 2026 SuperNova · 智能体服务平台</span><span>● 所有系统运行正常</span><span>人格、供应商、知识库元数据已连接后端 · 对话与文档处理待接入</span></footer>
  </div>
 </main>

 <div v-if="personaModal" class="modal-backdrop" @click.self="personaModal=false"><section class="modal"><div class="modal-title"><div><small>PERSONA CONFIGURATION</small><h2>{{editingId?'编辑人格':'创建新人格'}}</h2><p>配置人格提示词、模型参数和知识库关联。</p></div><button @click="personaModal=false"><X :size="18"/></button></div>
  <div class="form-grid">
   <label>人格名称 <b>*</b><input v-model="personaForm.name" maxlength="100" placeholder="例如：智能客服"></label>
   <label>调用标识（model） <b>*</b><input v-model="personaForm.slug" maxlength="80" placeholder="例如：customer-support"><small>仅允许小写字母、数字和连字符。</small></label>
   <label class="full">人格简介<input v-model="personaForm.description" maxlength="500" placeholder="简单描述这个人格的用途"></label>
   <label class="full">模型供应商<select v-model="personaForm.provider_id"><option value="">暂不绑定供应商</option><option v-for="provider in providers" :key="provider.id" :value="provider.id" :disabled="!provider.enabled">{{provider.name}}（{{provider.provider_type}}）</option></select><small>先在「模型供应商」中保存供应商地址和 API Key。</small></label>
   <label class="full">模型名称 <b>*</b><input v-model="personaForm.model_name" maxlength="120" placeholder="例如：deepseek-chat / gpt-4o-mini"></label>
   <label class="full">系统提示词 <b>*</b><textarea v-model="personaForm.system_prompt" rows="6" maxlength="20000" placeholder="定义 AI 的身份、目标、表达风格和行为边界..."></textarea></label>
   <label>Temperature<input v-model.number="personaForm.temperature" type="number" min="0" max="2" step="0.1"></label>
   <label>Top P<input v-model.number="personaForm.top_p" type="number" min="0" max="1" step="0.05"></label>
   <label>最大输出 Token<input v-model.number="personaForm.max_tokens" type="number" min="1" max="65536"></label>
   <div class="form-switch"><div><b>启用人格</b><small>停用后，后续 API 网关将拒绝调用此人格。</small></div><button class="toggle" :class="{on:personaForm.enabled}" @click="personaForm.enabled=!personaForm.enabled"></button></div>
   <div class="full"><b class="form-section-title">关联知识库</b><div v-if="kbs.length" class="checkbox-grid"><label v-for="kb in kbs" :key="kb.id"><input v-model="personaForm.knowledge_base_ids" type="checkbox" :value="kb.id"><span>{{kb.name}}</span></label></div><small v-else class="form-hint">尚无知识库，可先创建知识库后再关联。</small></div>
  </div>
  <div class="modal-actions"><button class="btn secondary" @click="personaModal=false">取消</button><button class="btn primary" @click="savePersona"><Check :size="15"/>保存人格</button></div>
 </section></div> <div v-if="kbModal" class="modal-backdrop" @click.self="kbModal=false"><section class="modal small-modal"><div class="modal-title"><div><small>KNOWLEDGE BASE</small><h2>创建知识库</h2><p>建立独立资料空间，后续可关联到不同人格。</p></div><button @click="kbModal=false"><X :size="18"/></button></div><div class="form-grid"><label class="full">知识库名称 <b>*</b><input v-model="kbForm.name" placeholder="例如：产品资料库"></label><label class="full">知识库描述<textarea v-model="kbForm.description" rows="3" placeholder="说明知识库包含哪些内容..."></textarea></label></div><div class="modal-actions"><button class="btn secondary" @click="kbModal=false">取消</button><button class="btn primary" @click="createKb"><Plus :size="15"/>创建知识库</button></div></section></div>
 <Transition name="toast"><div v-if="toast" class="toast"><CheckCircle2 :size="17"/>{{toast}}</div></Transition>
 <div v-if="providerModal" class="modal-backdrop" @click.self="providerModal=false"><section class="modal small-modal"><div class="modal-title"><div><small>MODEL PROVIDER</small><h2>{{editingProviderId?'编辑模型供应商':'添加模型供应商'}}</h2><p>API Key 会在服务端加密保存，读取接口不会返回明文。</p></div><button @click="providerModal=false"><X :size="18"/></button></div>
  <div class="form-grid">
   <label class="full">供应商名称 <b>*</b><input v-model="providerForm.name" maxlength="80" placeholder="例如：DeepSeek 主账户"></label>
   <label class="full">供应商类型<select v-model="providerForm.provider_type" @change="providerPresetChanged"><option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="qwen">通义千问（Qwen）</option><option value="anthropic">Anthropic</option><option value="openai_compatible">OpenAI 兼容接口</option></select></label>
   <label class="full">Base URL <b>*</b><input v-model="providerForm.base_url" maxlength="2048" placeholder="https://api.example.com/v1"><small>非本机地址必须使用 HTTPS；兼容接口可自定义。</small></label>
   <label class="full">API Key <b v-if="!editingProviderId">*</b><input v-model="providerForm.api_key" type="password" maxlength="4096" autocomplete="new-password" :placeholder="editingProviderId?'留空表示保留现有密钥':'请输入供应商 API Key'"><small v-if="editingProviderId">编辑时不回显旧密钥；填写新值会替换现有密钥。</small></label>
   <div class="form-switch full"><div><b>启用供应商</b><small>停用后可保留配置，但人格不应使用该供应商。</small></div><button class="toggle" :class="{on:providerForm.enabled}" @click="providerForm.enabled=!providerForm.enabled"></button></div>
  </div>
  <div class="modal-actions"><button class="btn secondary" @click="providerModal=false">取消</button><button class="btn primary" @click="saveProvider"><Check :size="15"/>保存供应商</button></div>
 </section></div>
 <div v-if="keyModal" class="modal-backdrop" @click.self="keyModal=false"><section class="modal small-modal"><div class="modal-title"><div><small>USER API KEY</small><h2>创建 API Key</h2><p>该密钥绑定当前用户和工作空间，可供后续多个 AI 人格共用。</p></div><button @click="keyModal=false"><X :size="18"/></button></div><div class="form-grid"><label class="full">密钥名称 <b>*</b><input v-model="newKeyName" maxlength="80" placeholder="例如：生产环境业务系统"></label></div><div class="modal-actions"><button class="btn secondary" @click="keyModal=false">取消</button><button class="btn primary" @click="createApiKey"><Plus :size="15"/>创建密钥</button></div></section></div>
 <div v-if="secretModal" class="modal-backdrop" @click.self="secretModal=false"><section class="modal small-modal"><div class="modal-title"><div><small>SECRET CREATED</small><h2>请立即保存 API Key</h2><p>出于安全考虑，关闭窗口后将无法再次查看完整密钥。</p></div><button @click="secretModal=false"><X :size="18"/></button></div><div class="key-field secret-field"><code>{{newApiKeySecret}}</code><button @click="copy(newApiKeySecret,'API Key 已复制')"><Copy :size="16"/></button></div><p class="warning"><AlertCircle :size="14"/>请将密钥保存在安全的密码管理器中，不要提交到代码仓库。</p><div class="modal-actions"><button class="btn secondary" @click="copy(newApiKeySecret,'API Key 已复制')"><Copy :size="15"/>复制密钥</button><button class="btn primary" @click="secretModal=false;newApiKeySecret=''">我已保存</button></div></section></div>
 <Transition name="toast"><div v-if="toast" class="toast"><CheckCircle2 :size="17"/>{{toast}}</div></Transition>
</div>
</template>
