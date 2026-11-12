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
      await request<Persona>(\`/personas/\${editingId.value}\`, 'PATCH', payload)
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
    await request<Persona>(\`/personas/\${persona.id}\`, 'PATCH', { enabled: !persona.enabled })
    await loadWorkspace()
    notify(persona.enabled ? '人格已停用' : '人格已启用')
  } catch (error) {
    notify(error instanceof Error ? error.message : '更新人格状态失败')
  }
}
async function deletePersona(persona: PersonaView) {
  if (!window.confirm(\`确定删除人格「\${persona.name}」吗？\`)) return
  try {
    await request<void>(\`/personas/\${persona.id}\`, 'DELETE')
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
      await request<ModelProvider>(\`/model-providers/\${editingProviderId.value}\`, 'PATCH', payload)
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
    await request<ModelProvider>(\`/model-providers/\${provider.id}\`, 'PATCH', { enabled: !provider.enabled })
    await loadWorkspace()
    notify(provider.enabled ? '供应商已停用' : '供应商已启用')
  } catch (error) {
    notify(error instanceof Error ? error.message : '更新供应商状态失败')
  }
}
async function deleteProvider(provider: ModelProvider) {
  if (!window.confirm(\`确定删除模型供应商「\${provider.name}」吗？\`)) return
  try {
    await request<void>(\`/model-providers/\${provider.id}\`, 'DELETE')
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
  if (!window.confirm(\`确定删除知识库「\${kb.name}」吗？关联关系也会一并移除。\`)) return
  try {
    await request<void>(\`/knowledge-bases/\${kb.id}\`, 'DELETE')
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
const baseUrl = \`\${API_BASE_URL}/v1\`
const example = \`curl \${baseUrl}/chat/completions \\
  -H "Authorization: Bearer YOUR_USER_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "customer-support",
    "messages": [{"role": "user", "content": "你好"}]
  }'\`
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
  if (!window.confirm(\`轮换「\${key.name}」后，旧密钥将立即失效。确定继续吗？\`)) return
  try {
    const result = await request<ApiKeyCreated>(\`/api-keys/\${key.id}/rotate\`, 'POST')
    newApiKeySecret.value = result.secret
    secretModal.value = true
    await loadWorkspace()
  } catch (error) {
    notify(error instanceof Error ? error.message : '轮换 API Key 失败')
  }
}
async function revokeKey(key: ApiKey) {
  if (!window.confirm(\`确定撤销 API Key「\${key.name}」吗？\`)) return
  try {
    await request<void>(\`/api-keys/\${key.id}\`, 'DELETE')
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
<div class="app-shell">
 <div v-if="mobileOpen" class="scrim" @click="mobileOpen=false"></div>
 <aside class="sidebar" :class="{'sidebar-open':mobileOpen}">
  <div class="brand"><img src="/logo.svg" alt="SuperNova"><div><b>SuperNova</b><small>智能体平台</small></div><i>BETA</i></div>
  <button class="workspace" @click="notify('工作空间切换将在正式版开放')"><span>S</span><div><b>我的工作空间</b><small>个人空间</small></div><ChevronDown :size="15"/></button>
  <nav><div v-for="g in nav" :key="g.label" class="nav-group"><small>{{g.label}}</small><button v-for="n in g.items" :key="n.key" :class="{active:activeNav===n.key}" @click="go(n.key)"><component :is="n.icon" :size="18"/>{{n.label}}<em v-if="n.key==='personas'">{{personas.length}}</em></button></div></nav>
  <div class="sidebar-bottom"><div class="help"><LifeBuoy :size="17"/><div><b>需要帮助？</b><small>查看使用指南与接口文档</small></div></div><button class="side-setting" @click="go('settings')"><Settings2 :size="17"/>账户设置</button><div class="profile"><span>M</span><div><b>Master</b><small>免费体验版</small></div><button @click="notify('演示原型暂未接入登录')"><LogOut :size="16"/></button></div></div>
 </aside>
 <main class="main">
  <header><div class="crumb"><button class="menu-btn" @click="mobileOpen=true"><Menu :size="20"/></button><span>SuperNova</span><ChevronRight :size="14"/><b>{{titles[page]}}</b></div><div class="top-actions"><span class="demo"><i></i>演示环境</span><button @click="notify('帮助中心将在后续版本接入')"><CircleHelp :size="18"/></button><button @click="notify('暂时没有新通知')"><Bell :size="18"/></button><span class="top-avatar">M</span></div></header>
  <div class="content">
   <div class="heading"><div><small>SUPERNOVA CONSOLE</small><h1>{{titles[page]}}</h1><p>{{descriptions[page]}}</p></div><div class="heading-actions">
    <button v-if="page==='overview'||page==='personas'" class="btn primary" @click="editPersona()"><Plus :size="16"/>新建人格</button>
    <button v-else-if="page==='knowledge'" class="btn primary" @click="kbModal=true"><Plus :size="16"/>新建知识库</button>
    <template v-else-if="page==='knowledgeDetail'"><button class="btn secondary" @click="go('knowledge')">返回知识库</button><button class="btn primary" @click="upload"><Upload :size="16"/>上传文档</button></template>
    <button v-else-if="page==='api'" class="btn secondary" @click="copy(baseUrl,'Base URL 已复制')"><Copy :size="16"/>复制 Base URL</button>
    <button v-else-if="page==='usage'" class="btn secondary" @click="notify('报表导出将在正式版开放')"><Download :size="16"/>导出报表</button>
   </div></div>

   <template v-if="page==='overview'">
    <section class="hero"><div><span><Sparkles :size="14"/>让 AI 助手开始为你工作</span><h2>你好，Master <i>✦</i></h2><p>你的智能体工作空间已准备就绪。创建人格、连接知识库，开始构建专属 AI 助手。</p><button @click="go('personas')">管理我的人格 <ArrowRight :size="15"/></button></div><div class="hero-art"><div class="orbit one"></div><div class="orbit two"></div><img src="/logo.svg" alt=""><i class="orb-a"></i><i class="orb-b"></i><i class="orb-c"></i></div></section>
    <section class="stats">
     <article><div><span class="ico blue"><Bot :size="18"/></span>我的人格<button @click="go('personas')"><MoreHorizontal :size="17"/></button></div><strong>{{personas.length}}<small>个</small></strong><footer><b>{{personas.filter(p=>p.enabled).length}} 个运行中</b><span>共创建</span></footer></article>
     <article><div><span class="ico purple"><Zap :size="18"/></span>本月 Token 消耗<button @click="go('usage')"><MoreHorizontal :size="17"/></button></div><strong>1.28<small>M</small></strong><footer><b>↗ 18.6%</b><span>较上月</span></footer></article>
     <article><div><span class="ico green"><Wallet :size="18"/></span>剩余额度<button @click="go('usage')"><MoreHorizontal :size="17"/></button></div><strong>8.72<small>M</small></strong><footer><span>可用 Token</span><a @click="go('usage')">查看账单 →</a></footer></article>
     <article><div><span class="ico amber"><Database :size="18"/></span>知识库文档<button @click="go('knowledge')"><MoreHorizontal :size="17"/></button></div><strong>{{totalDocs}}<small>份</small></strong><footer><span>{{kbs.length}} 个知识库</span><a @click="go('knowledge')">管理知识库 →</a></footer></article>
    </section>
    <section class="dashboard"><article class="panel chart-panel"><div class="panel-title"><div><b>Token 使用趋势</b><small>近 7 天的模型调用消耗</small></div><a @click="go('usage')">查看详情 →</a></div><div class="chart-total"><strong>428,690</strong><span>Tokens</span><em>↗ 12.8%</em></div><div class="chart"><div class="y-axis"><span>100K</span><span>75K</span><span>50K</span><span>25K</span><span>0</span></div><svg viewBox="0 0 680 190" preserveAspectRatio="none"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4788f5" stop-opacity=".2"/><stop offset="1" stop-color="#4788f5" stop-opacity="0"/></linearGradient></defs><path class="grid" d="M0 12H680 M0 52H680 M0 92H680 M0 132H680 M0 172H680"/><path d="M0 145 C35 140 45 118 82 125 S130 106 164 118 S210 95 245 105 S292 76 328 92 S374 61 410 80 S458 68 491 74 S540 38 573 54 S633 23 680 31 L680 180 L0 180Z" fill="url(#fill)"/><path d="M0 145 C35 140 45 118 82 125 S130 106 164 118 S210 95 245 105 S292 76 328 92 S374 61 410 80 S458 68 491 74 S540 38 573 54 S633 23 680 31" fill="none" stroke="#4788f5" stroke-width="3"/></svg></div><div class="x-axis"><span>11/06</span><span>11/07</span><span>11/08</span><span>11/09</span><span>11/10</span><span>11/11</span><span>11/12</span></div></article>
     <article class="panel resource"><div class="panel-title"><div><b>资源概览</b><small>当前工作空间资源使用情况</small></div><Gauge :size="18"/></div><div class="resource-line"><div><span>人格运行状态</span><b>{{personas.filter(p=>p.enabled).length}}/{{personas.length}}</b></div><i><span :style="{width:(personas.filter(p=>p.enabled).length/personas.length*100)+'%'}"></span></i><small>已启用的人格</small></div><div class="resource-line"><div><span>知识库索引</span><b>2/3</b></div><i><span class="violet" style="width:66%"></span></i><small>已完成索引的知识库</small></div><div class="secure-note"><ShieldCheck :size="16"/>数据按用户空间隔离，其他用户无法访问你的资源。</div></article></section>
    <section class="panel"><div class="panel-title"><div><b>最近使用的人格</b><small>快速进入人格配置</small></div><a @click="go('personas')">查看全部 →</a></div><div class="persona-shortcuts"><button v-for="p in personas" :key="p.id" @click="editPersona(p)"><span class="persona-icon" :class="p.color"><Bot :size="18"/></span><span><b>{{p.name}}</b><small>{{p.model}}</small></span><i :class="{off:!p.enabled}"></i><ChevronRight :size="15"/></button></div></section>
    <section class="panel"><div class="panel-title"><div><b>最近调用</b><small>近期 API 请求记录（演示数据）</small></div><a @click="go('usage')">全部记录 →</a></div><div class="table-scroll"><table><thead><tr><th>人格</th><th>请求内容</th><th>Token 消耗</th><th>响应时间</th><th>状态</th><th>时间</th></tr></thead><tbody><tr><td>智能客服</td><td>退换货政策咨询</td><td>1,248</td><td>1.2s</td><td><label class="success">成功</label></td><td>10:42:18</td></tr><tr><td>品牌顾问</td><td>生成产品介绍文案</td><td>2,806</td><td>2.4s</td><td><label class="success">成功</label></td><td>10:36:05</td></tr><tr><td>社群助手</td><td>活动时间与地点</td><td>864</td><td>0.9s</td><td><label class="success">成功</label></td><td>09:58:31</td></tr></tbody></table></div></section>
   </template>

   <template v-else-if="page==='personas'">
    <section class="metric-strip"><div><small>全部人格</small><b>{{personas.length}}</b></div><div><small>运行中</small><b class="green-text">{{personas.filter(p=>p.enabled).length}}</b></div><div><small>已停用</small><b>{{personas.filter(p=>!p.enabled).length}}</b></div><div><small>调用方式</small><b class="small-metric">用户级 API Key</b></div></section>
    <section class="panel no-pad"><div class="toolbar"><label class="search"><Search :size="16"/><input v-model="query" placeholder="搜索人格名称或调用标识..."></label><button class="btn secondary small" @click="notify('筛选功能将在正式版开放')"><Filter :size="14"/>筛选</button></div><div v-if="filteredPersonas.length"><article v-for="p in filteredPersonas" :key="p.id" class="persona-row"><span class="persona-icon large" :class="p.color"><Bot :size="21"/></span><div class="persona-detail"><div><b>{{p.name}}</b><label :class="p.enabled?'running':'paused'"><i></i>{{p.enabled?'运行中':'已停用'}}</label></div><p>{{p.description}}</p><small><code>{{p.slug}}</code><span><Cpu :size="12"/> {{p.model}}</span></small></div><div class="row-actions"><button @click="togglePersona(p)" :aria-label="p.enabled?'停用':'启用'"><Zap :size="16"/></button><button @click="editPersona(p)" aria-label="编辑"><Pencil :size="16"/></button><button @click="deletePersona(p)" aria-label="删除"><Trash2 :size="16"/></button></div></article></div><div v-else class="empty"><Search :size="28"/><b>没有找到匹配的人格</b><small>试试其他关键词，或创建一个新人格。</small></div></section>
    <div class="info"><AlertCircle :size="17"/><div><b>关于人格调用</b><small>所有人格共用当前用户的 API Key。第三方请求时，将人格调用标识填写在 model 字段中，例如 customer-support。</small></div></div>
   </template>

   <template v-else-if="page==='knowledge'">
    <section class="kb-summary"><div><Database :size="18"/><small>知识库总数</small><b>{{kbs.length}} 个</b></div><div><FileText :size="18"/><small>文档总数</small><b>{{totalDocs}} 份</b></div><div><CheckCircle2 :size="18"/><small>索引状态</small><b>正常运行</b></div></section>
    <section class="kb-grid"><article v-for="k in kbs" :key="k.id" class="kb-card"><div class="kb-card-head"><span class="kb-icon" :class="k.color"><Database :size="20"/></span><button @click="notify('更多操作将在正式版开放')"><MoreHorizontal :size="18"/></button></div><h3>{{k.name}}</h3><p>{{k.description||'暂无描述'}}</p><div class="kb-meta"><span><FileText :size="13"/> {{k.docs.length}} 份文档</span><span>最近更新</span></div><footer><small><i></i>{{k.docs.some(d=>d.status!=='已完成')?'处理中':'已就绪'}}</small><a @click="openKb(k)">管理知识库 →</a></footer></article><button class="kb-create" @click="kbModal=true"><span><Plus :size="22"/></span><b>创建知识库</b><small>上传文档或导入网页链接</small></button></section>
    <div class="info"><AlertCircle :size="17"/><div><b>知识库隔离说明</b><small>每个知识库归属于当前用户。正式版本将支持文档解析、文本切片、向量化检索，并可绑定到一个或多个指定人格。</small></div></div>
   </template>

   <template v-else-if="page==='knowledgeDetail'">
    <section class="panel kb-detail"><span class="kb-icon" :class="selectedKb.color"><Database :size="23"/></span><div><h2>{{selectedKb.name}}</h2><p>{{selectedKb.description}}</p><small>{{selectedKb.docs.length}} 份文档 · 可用</small></div><button class="btn primary" @click="upload"><Upload :size="15"/>上传文档</button></section>
    <section class="panel"><div class="panel-title"><div><b>文档列表</b><small>支持 PDF、DOCX、TXT、Markdown、CSV、XLSX 等常见格式（原型展示）</small></div><button class="btn secondary small" @click="upload"><Plus :size="14"/>添加文档</button></div><div v-if="selectedKb.docs.length" class="table-scroll"><table><thead><tr><th>文件名称</th><th>文件大小</th><th>类型</th><th>处理状态</th><th></th></tr></thead><tbody><tr v-for="d in selectedKb.docs" :key="d.name"><td><FileText :size="15" class="file-icon"/> {{d.name}}</td><td>{{d.size}}</td><td>{{d.kind}}</td><td><label :class="d.status==='已完成'?'success':'pending'">{{d.status}}</label></td><td><button class="icon-action" @click="deleteDoc(d)"><Trash2 :size="15"/></button></td></tr></tbody></table></div><div v-else class="empty"><Upload :size="28"/><b>还没有添加文档</b><small>上传产品资料、常见问题或其他文件，构建专属知识库。</small><button class="btn primary" @click="upload">选择文件</button></div></section>
    <section class="panel binding"><div class="panel-title"><div><b>已关联人格</b><small>正式版可在此设置哪些人格能够检索当前知识库。</small></div></div><span v-for="p in personas.slice(0,2)" :key="p.id"><Bot :size="14"/> {{p.name}} <Check :size="13"/></span><a @click="notify('人格关联功能将在后续接入')">管理关联 →</a></section>
    <input ref="fileInput" class="hidden" type="file" accept=".pdf,.doc,.docx,.txt,.md,.csv,.xls,.xlsx" @change="fileSelected">
   </template>

   <template v-else-if="page==='api'">
    <section class="api-notice"><ShieldCheck :size="21"/><div><b>一个用户密钥，调用多个独立人格</b><p>API Key 绑定你的用户账户，不绑定某一个机器人。通过请求中的 model 字段选择你创建的人格。</p></div></section>
    <section class="api-grid"><div class="api-main">
     <article class="panel"><div class="panel-title"><div><b>用户级 API Key</b><small>所有已授权人格共用此密钥。</small></div><label class="paused">演示密钥</label></div><small class="field-label">API Key</small><div class="key-field"><code>{{keyDisplay}}</code><div><button @click="keyVisible=!keyVisible"><component :is="keyVisible?EyeOff:Eye" :size="16"/></button><button @click="copy(apiKey,'API Key 已复制')"><Copy :size="16"/></button></div></div><p class="warning"><AlertCircle :size="14"/>原型演示密钥，不可用于真实 API 请求。正式版将由服务端生成并仅展示一次。</p><div class="key-actions"><button class="btn secondary" @click="rotateKey"><Zap :size="14"/>轮换密钥</button><button class="btn primary" @click="notify('正式密钥创建功能将在身份认证阶段接入')"><Plus :size="14"/>创建 API Key</button></div></article>
     <article class="panel"><div class="panel-title"><div><b>接口连接信息</b><small>兼容 OpenAI Chat Completions 接口格式。</small></div><label class="running">OpenAI 兼容</label></div><div class="endpoint"><div><small>Base URL</small><code>{{baseUrl}}</code></div><button @click="copy(baseUrl,'Base URL 已复制')"><Copy :size="15"/></button></div><div class="endpoint"><div><small>API 路径</small><code>/chat/completions</code></div><button @click="copy('/chat/completions')"><Copy :size="15"/></button></div><div class="endpoint"><div><small>认证方式</small><code>Authorization: Bearer YOUR_USER_API_KEY</code></div><button @click="copy('Authorization: Bearer YOUR_USER_API_KEY')"><Copy :size="15"/></button></div></article>
     <article class="panel"><div class="panel-title"><div><b>快速调用示例</b><small>将 model 替换为你设置的人格调用标识。</small></div><a @click="copy(example,'示例代码已复制')"><Copy :size="14"/>复制代码</a></div><pre>{{example}}</pre></article>
    </div><aside class="api-side"><article class="panel"><div class="panel-title"><div><b>可调用的人格</b><small>当前账户下已创建的人格</small></div></div><div v-for="p in personas" :key="p.id" class="model-row"><span class="persona-icon" :class="p.color"><Bot :size="15"/></span><div><b>{{p.name}}</b><small>{{p.slug}}</small></div><i :class="{off:!p.enabled}"></i></div><a @click="go('personas')">管理人格 →</a></article><article class="panel tips"><LifeBuoy :size="18"/><b>接入提示</b><ul><li>API Key 是用户级密钥，不要公开分享。</li><li>model 填写人格调用标识，不是模型供应商名称。</li><li>不同人格可配置不同模型、提示词和知识库。</li><li>Token 用量统一计入当前用户账户。</li></ul></article></aside></section>
   </template>

   <template v-else-if="page==='usage'">
    <section class="billing"><article class="featured"><small>本月 Token 消耗</small><b>1,284,620</b><span>↗ 较上月增长 18.6%</span></article><article><small>剩余可用额度</small><b>8,715,380 <i>Tokens</i></b><span>当前体验额度</span><div class="quota"><i></i></div></article><article><small>本月请求次数</small><b>3,842 <i>次</i></b><span>平均响应时间 1.6 秒</span></article></section>
    <section class="panel"><div class="panel-title"><div><b>Token 消耗趋势</b><small>按时间查看模型输入与输出 Token 消耗</small></div><div class="tabs"><button v-for="t in ['日','周','月']" :key="t" :class="{active:period===t}" @click="period=t">{{t}}</button></div></div><div class="bars"><div class="bar-axis"><span>100K</span><span>75K</span><span>50K</span><span>25K</span><span>0</span></div><div class="bar-area"><div v-for="(h,i) in bars" :key="i" class="bar-col"><div class="bar-stack"><i :style="{height:(h*.68)+'%'}"></i><i :style="{height:(h*.32)+'%'}"></i></div><small>{{labels[i]}}</small></div></div></div><div class="legend"><span></span>输入 Token <i></i>输出 Token</div></section>
    <section class="panel"><div class="panel-title"><div><b>调用明细</b><small>最近的接口请求与 Token 计量记录</small></div><button class="btn secondary small" @click="notify('明细导出将在正式版开放')"><Download :size="14"/>导出明细</button></div><div class="table-scroll"><table><thead><tr><th>时间</th><th>人格</th><th>模型</th><th>输入 Token</th><th>输出 Token</th><th>总计</th><th>状态</th></tr></thead><tbody><tr><td>11-12 10:42:18</td><td>智能客服</td><td>deepseek-chat</td><td>862</td><td>386</td><td>1,248</td><td><label class="success">成功</label></td></tr><tr><td>11-12 10:36:05</td><td>品牌顾问</td><td>gpt-4o-mini</td><td>1,906</td><td>900</td><td>2,806</td><td><label class="success">成功</label></td></tr><tr><td>11-12 09:58:31</td><td>社群助手</td><td>qwen-plus</td><td>620</td><td>244</td><td>864</td><td><label class="success">成功</label></td></tr></tbody></table></div></section>
    <section class="plan"><div><small>当前方案</small><h3>免费体验版</h3><p>用于体验人格、知识库和 API 调用功能。套餐与真实扣费尚未接入。</p></div><div><b>10M</b><small>体验 Token 额度</small><button class="btn primary" @click="notify('套餐购买功能将在计费模块上线后开放')">查看套餐 →</button></div></section>
   </template>

   <template v-else-if="page==='settings'">
    <section class="panel settings"><div class="panel-title"><div><b>基本资料</b><small>管理你的账户展示信息</small></div></div><div class="account"><span>M</span><div><b>Master</b><small>个人工作空间所有者</small></div><button class="btn secondary small" @click="notify('头像上传将在正式版开放')">更换头像</button></div><div class="settings-fields"><label>账户名称<input value="Master" readonly></label><label>登录邮箱<input value="尚未绑定" readonly></label><label>账户类型<input value="个人账户" readonly></label></div></section>
    <section class="panel settings"><div class="panel-title"><div><b>安全与隐私</b><small>保护你的账户和 API 资源</small></div></div><div class="setting-row"><ShieldCheck :size="18"/><div><b>登录密码</b><small>定期更新密码，保护账户安全</small></div><button class="btn secondary small" @click="notify('密码管理将在登录模块接入')">修改密码</button></div><div class="setting-row"><KeyRound :size="18"/><div><b>API 密钥</b><small>管理第三方应用的访问凭证</small></div><a @click="go('api')">前往管理 →</a></div></section>
    <section class="panel settings"><div class="panel-title"><div><b>通知偏好</b><small>选择你希望接收的系统通知</small></div></div><div class="setting-row"><Bell :size="18"/><div><b>用量提醒</b><small>当 Token 额度即将耗尽时提醒我</small></div><span class="toggle on"></span></div><div class="setting-row"><MessageSquare :size="18"/><div><b>系统公告</b><small>接收产品更新和维护通知</small></div><span class="toggle"></span></div></section>
   </template>
   <footer class="page-footer"><span>© 2026 SuperNova · 智能体服务平台</span><span>● 所有系统运行正常</span><span>交互原型 · 业务数据尚未连接后端</span></footer>
  </div>
 </main>

 <div v-if="personaModal" class="modal-backdrop" @click.self="personaModal=false"><section class="modal"><div class="modal-title"><div><small>PERSONA CONFIGURATION</small><h2>{{editingId?'编辑人格':'创建新人格'}}</h2><p>定义 AI 助手的身份、模型和行为方式。</p></div><button @click="personaModal=false"><X :size="18"/></button></div><div class="form-grid"><label>人格名称 <b>*</b><input v-model="personaForm.name" placeholder="例如：智能客服"></label><label>调用标识（model） <b>*</b><input v-model="personaForm.slug" placeholder="例如：customer-support"><small>第三方请求通过此标识选择人格。</small></label><label class="full">人格简介<input v-model="personaForm.description" placeholder="简单描述这个人格的用途"></label><label class="full">模型<select v-model="personaForm.model"><option>deepseek-chat</option><option>deepseek-reasoner</option><option>gpt-4o-mini</option><option>qwen-plus</option><option>claude-3-7-sonnet</option></select><small>模型供应商和密钥将在模型配置模块中统一管理。</small></label><label class="full">系统提示词<textarea v-model="personaForm.prompt" rows="5" placeholder="描述 AI 的身份、目标、表达风格和行为边界..."></textarea></label><div class="form-switch full"><div><b>启用人格</b><small>停用后，API 将无法通过该标识调用此人格。</small></div><button class="toggle" :class="{on:personaForm.enabled}" @click="personaForm.enabled=!personaForm.enabled"></button></div></div><div class="modal-actions"><button class="btn secondary" @click="personaModal=false">取消</button><button class="btn primary" @click="savePersona"><Check :size="15"/>保存人格</button></div></section></div>
 <div v-if="kbModal" class="modal-backdrop" @click.self="kbModal=false"><section class="modal small-modal"><div class="modal-title"><div><small>KNOWLEDGE BASE</small><h2>创建知识库</h2><p>建立独立资料空间，后续可关联到不同人格。</p></div><button @click="kbModal=false"><X :size="18"/></button></div><div class="form-grid"><label class="full">知识库名称 <b>*</b><input v-model="kbForm.name" placeholder="例如：产品资料库"></label><label class="full">知识库描述<textarea v-model="kbForm.description" rows="3" placeholder="说明知识库包含哪些内容..."></textarea></label></div><div class="modal-actions"><button class="btn secondary" @click="kbModal=false">取消</button><button class="btn primary" @click="createKb"><Plus :size="15"/>创建知识库</button></div></section></div>
 <input v-if="page!=='knowledgeDetail'" ref="fileInput" class="hidden" type="file" accept=".pdf,.doc,.docx,.txt,.md,.csv,.xls,.xlsx" @change="fileSelected">
 <Transition name="toast"><div v-if="toast" class="toast"><CheckCircle2 :size="17"/>{{toast}}</div></Transition>
</div>
</template>
