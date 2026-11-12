<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  Activity, AlertCircle, ArrowRight, Bell, Bot, Check, CheckCircle2,
  ChevronDown, ChevronRight, CircleHelp, Copy, Cpu, Database, Download, FileText,
  Filter, Gauge, KeyRound, LayoutDashboard, LifeBuoy, LogOut, Menu, MessageSquare,
  MoreHorizontal, Pencil, Plus, Search, Server, Settings2, ShieldCheck, Sparkles,
  Trash2, Upload, Wallet, X, Zap,
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
  type BillingEntitlements,
  type BillingLedgerTransaction,
  type BillingPlan,
  type BillingShadowUsageSummary,
  type PlatformModel,
  type KnowledgeBase,
  type KnowledgeDocument,
  type KnowledgeSearchResponse,
  type KnowledgeUsageSummary,
  type PlatformEmbeddingModel,
  type ModelProvider,
  type Persona,
  type Tenant,
  type User,
  type UsageRecord,
  type UsageSummary,
} from './lib/api'

type Page = 'overview' | 'personas' | 'providers' | 'knowledge' | 'knowledgeDetail' | 'api' | 'billing' | 'usage' | 'settings'
type PersonaView = Persona & { color: string }
type KnowledgeBaseView = KnowledgeBase & { color: string; docs: KnowledgeDocument[] }

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
  billing: '套餐与额度',
  usage: '用量与账单',
  settings: '账户设置',
}
const descriptions: Record<Page, string> = {
  overview: '查看工作空间中的人格、模型供应商和资源状态。',
  personas: '创建和管理独立 AI 人格，为每个人格配置模型与行为方式。',
  providers: '集中管理模型供应商、兼容接口地址和加密保存的 API Key。',
  knowledge: '集中管理知识库资料空间，并关联到一个或多个 AI 人格。',
  knowledgeDetail: '管理文档导入、索引状态、混合检索与 RAG 来源。',
  api: '使用一个用户级 API Key，通过 OpenAI 兼容接口调用多个独立人格。',
  billing: '查看当前订阅、实时额度预留、影子计价和账本记录；当前不会产生真实扣款。',
  usage: '查看真实模型调用次数、输入输出 Token、响应时间和未计量请求。',
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
      { key: 'billing' as Page, label: '套餐与额度', icon: Wallet },
      { key: 'usage' as Page, label: '用量记录', icon: Activity },
    ],
  },
]
const activeNav = computed(() => page.value === 'knowledgeDetail' ? 'knowledge' : page.value)

const personas = ref<PersonaView[]>([])
const providers = ref<ModelProvider[]>([])
const platformModels = ref<PlatformModel[]>([])
const embeddingModels = ref<PlatformEmbeddingModel[]>([])
const billingPlans = ref<BillingPlan[]>([])
const billingEntitlements = ref<BillingEntitlements | null>(null)
const billingUsage = ref<BillingShadowUsageSummary | null>(null)
const billingLedger = ref<BillingLedgerTransaction[]>([])
const billingLoading = ref(false)
const kbs = ref<KnowledgeBaseView[]>([])
const knowledgeDocuments = ref<KnowledgeDocument[]>([])
const knowledgeUsage = ref<KnowledgeUsageSummary | null>(null)
const knowledgeBusy = ref(false)
const knowledgeSearchQuery = ref('')
const knowledgeSearchHits = ref<KnowledgeSearchResponse['hits']>([])
const knowledgeSearchBusy = ref(false)
const apiKeys = ref<ApiKey[]>([])
const usageSummary = ref<UsageSummary | null>(null)
const usageRecords = ref<UsageRecord[]>([])
const usageLoading = ref(false)
const selectedId = ref('')
const selectedKb = computed<KnowledgeBaseView>(() =>
  kbs.value.find(k => k.id === selectedId.value) ??
  kbs.value[0] ??
  { id: '', name: '', description: '', embedding_source: 'platform', platform_embedding_model_id: null, embedding_provider_id: null, embedding_model_name: null, embedding_dimensions: 1536, chunk_size: 800, chunk_overlap: 120, retrieval_top_k: 5, retrieval_score_threshold: 0.15, enabled: true, documents_count: 0, created_at: '', updated_at: '', color: 'blue', docs: [] },
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

function currencyMinorUnitExponent(currency: string): number {
  try {
    return new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2
  } catch {
    return 2
  }
}
function formatMinorAmount(
  value: string | number,
  currency: string,
  exponent = currencyMinorUnitExponent(currency),
): string {
  return (Number(value) / 10 ** exponent).toFixed(exponent)
}

async function loadWorkspace() {
  const [personaData, providerData, platformData, embeddingData, knowledgeData, keyData] = await Promise.all([
    request<Persona[]>('/personas'),
    request<ModelProvider[]>('/model-providers'),
    request<PlatformModel[]>('/platform/models'),
    request<PlatformEmbeddingModel[]>('/platform/embedding-models'),
    request<KnowledgeBase[]>('/knowledge-bases'),
    request<ApiKey[]>('/api-keys'),
  ])
  personas.value = personaData.map((persona, index) => ({ ...persona, color: colorFor(index) }))
  providers.value = providerData
  platformModels.value = platformData
  embeddingModels.value = embeddingData
  kbs.value = knowledgeData.map((kb, index) => ({ ...kb, color: colorFor(index), docs: [] }))
  apiKeys.value = keyData
  if (!kbs.value.some(kb => kb.id === selectedId.value)) selectedId.value = kbs.value[0]?.id ?? ''
}

async function loadKnowledgeDetail(showError = false) {
  if (!selectedId.value) return
  knowledgeBusy.value = true
  try {
    const [documents, usage] = await Promise.all([
      request<KnowledgeDocument[]>(`/knowledge-bases/${selectedId.value}/documents`),
      request<KnowledgeUsageSummary>(`/knowledge-bases/${selectedId.value}/usage`),
    ])
    knowledgeDocuments.value = documents
    knowledgeUsage.value = usage
    const selected = kbs.value.find(kb => kb.id === selectedId.value)
    if (selected) selected.documents_count = usage.document_count
  } catch (error) {
    if (showError) notify(error instanceof Error ? error.message : '读取知识库文档失败')
  } finally {
    knowledgeBusy.value = false
  }
}
async function loadBilling(showError = false) {
  if (!authenticated.value) return
  billingLoading.value = true
  try {
    const [plans, entitlements, usage, ledger] = await Promise.all([
      request<BillingPlan[]>('/billing/plans'),
      request<BillingEntitlements>('/billing/entitlements'),
      request<BillingShadowUsageSummary>('/billing/usage'),
      request<BillingLedgerTransaction[]>('/billing/ledger?limit=10'),
    ])
    billingPlans.value = plans
    billingEntitlements.value = entitlements
    billingUsage.value = usage
    billingLedger.value = ledger
  } catch (error) {
    if (showError) notify(error instanceof Error ? error.message : '读取套餐权益失败')
  } finally {
    billingLoading.value = false
  }
}
const apiRequestQuota = computed(() =>
  billingEntitlements.value?.resources.find(item => item.resource_code === 'api_requests') ?? null,
)
const platformInputQuota = computed(() =>
  billingEntitlements.value?.resources.find(item => item.resource_code === 'platform_input_tokens') ?? null,
)
const platformOutputQuota = computed(() =>
  billingEntitlements.value?.resources.find(item => item.resource_code === 'platform_output_tokens') ?? null,
)
async function loadUsage(showError = false) {
  if (!authenticated.value) return
  usageLoading.value = true
  try {
    const [summary, records] = await Promise.all([
      request<UsageSummary>('/usage/summary'),
      request<UsageRecord[]>('/usage/records?limit=10'),
    ])
    usageSummary.value = summary
    usageRecords.value = records
  } catch (error) {
    if (showError) notify(error instanceof Error ? error.message : '读取 Token 用量失败')
  } finally {
    usageLoading.value = false
  }
}
async function loadIdentity() {
  const identity = await request<{ user: User; tenant: Tenant }>('/auth/me')
  currentUser.value = identity.user
  currentTenant.value = identity.tenant
  authenticated.value = true
  await loadWorkspace()
  await loadBilling()
  await loadUsage()
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
  if (next === 'usage' || next === 'overview') void loadUsage()
  if (next === 'billing') void loadBilling()
}

const personaModal = ref(false)
const editingId = ref<string | null>(null)
const personaForm = ref({
  name: '',
  slug: '',
  description: '',
  system_prompt: '',
  model_source: 'platform' as 'platform' | 'byok',
  provider_id: '',
  platform_model_id: '',
  model_name: '',
  temperature: 0.7,
  top_p: 1,
  max_tokens: 2048,
  knowledge_base_ids: [] as string[],
  enabled: true,
})
function newPersonaSlug(): string {
  return `persona-${crypto.randomUUID().slice(0, 8)}`
}
function editPersona(persona?: PersonaView) {
  editingId.value = persona?.id ?? null
  personaForm.value = persona
    ? {
        name: persona.name,
        slug: persona.slug,
        description: persona.description,
        system_prompt: persona.system_prompt,
        model_source: persona.model_source,
        provider_id: persona.provider_id ?? '',
        platform_model_id: persona.platform_model_id ?? '',
        model_name: persona.model_name,
        temperature: persona.temperature,
        top_p: persona.top_p,
        max_tokens: persona.max_tokens,
        knowledge_base_ids: [...persona.knowledge_base_ids],
        enabled: persona.enabled,
      }
    : {
        name: '',
        slug: newPersonaSlug(),
        description: '',
        system_prompt: '',
        model_source: 'platform',
        provider_id: '',
        platform_model_id: platformModels.value[0]?.id ?? '',
        model_name: '',
        temperature: 0.7,
        top_p: 1,
        max_tokens: 2048,
        knowledge_base_ids: [],
        enabled: true,
      }
  personaModal.value = true
}
function onPersonaModelSourceChange() {
  if (personaForm.value.model_source === 'platform') {
    personaForm.value.provider_id = ''
    if (!personaForm.value.platform_model_id) {
      personaForm.value.platform_model_id = platformModels.value[0]?.id ?? ''
    }
  } else {
    personaForm.value.platform_model_id = ''
    if (!personaForm.value.provider_id) personaForm.value.provider_id = providers.value[0]?.id ?? ''
  }
}
async function savePersona() {
  if (!personaForm.value.name.trim() || !personaForm.value.slug.trim() ||
      !personaForm.value.system_prompt.trim()) {
    notify('请填写人格名称、调用标识和系统提示词')
    return
  }
  if (personaForm.value.model_source === 'platform' && !personaForm.value.platform_model_id) {
    notify('当前没有可用的平台模型，请联系平台管理员配置')
    return
  }
  if (personaForm.value.model_source === 'byok' &&
      (!personaForm.value.provider_id || !personaForm.value.model_name.trim())) {
    notify('BYOK 模式必须选择模型供应商并填写上游模型名称')
    return
  }
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(personaForm.value.slug.trim())) {
    notify('调用标识只能使用小写英文字母、数字和连字符')
    return
  }
  if (personaForm.value.temperature < 0 || personaForm.value.temperature > 2 ||
      personaForm.value.top_p < 0 || personaForm.value.top_p > 1 ||
      !Number.isInteger(personaForm.value.max_tokens) ||
      personaForm.value.max_tokens < 1 || personaForm.value.max_tokens > 65536) {
    notify('模型参数超出允许范围，请检查 Temperature、Top P 和最大输出 Token')
    return
  }
  const payload: Record<string, unknown> = {
    ...personaForm.value,
    name: personaForm.value.name.trim(),
    slug: personaForm.value.slug.trim(),
    description: personaForm.value.description.trim(),
    system_prompt: personaForm.value.system_prompt.trim(),
    model_source: personaForm.value.model_source,
    provider_id: personaForm.value.model_source === 'byok' ? personaForm.value.provider_id : null,
    platform_model_id: personaForm.value.model_source === 'platform' ? personaForm.value.platform_model_id : null,
  }
  if (personaForm.value.model_source === 'platform') {
    delete payload.model_name
  } else {
    payload.model_name = personaForm.value.model_name.trim()
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
const newKnowledgeBaseForm = () => ({
  name: '',
  description: '',
  embedding_source: 'platform' as 'platform' | 'byok',
  platform_embedding_model_id: embeddingModels.value[0]?.id ?? '',
  embedding_provider_id: '',
  embedding_model_name: '',
  embedding_dimensions: 1536,
  chunk_size: 800,
  chunk_overlap: 120,
  retrieval_top_k: 5,
  retrieval_score_threshold: 0.15,
  enabled: true,
})
const kbForm = ref(newKnowledgeBaseForm())
async function createKb() {
  if (!kbForm.value.name.trim()) {
    notify('请填写知识库名称')
    return
  }
  if (kbForm.value.embedding_source === 'platform' && !kbForm.value.platform_embedding_model_id) {
    notify('平台尚未配置 Embedding 模型，请联系管理员，或选择 BYOK 模式')
    return
  }
  if (kbForm.value.embedding_source === 'byok' &&
      (!kbForm.value.embedding_provider_id || !kbForm.value.embedding_model_name.trim())) {
    notify('BYOK 模式必须选择支持 Embedding 的供应商并填写模型名称')
    return
  }
  const payload = {
    ...kbForm.value,
    name: kbForm.value.name.trim(),
    description: kbForm.value.description.trim(),
    platform_embedding_model_id: kbForm.value.embedding_source === 'platform'
      ? kbForm.value.platform_embedding_model_id : null,
    embedding_provider_id: kbForm.value.embedding_source === 'byok'
      ? kbForm.value.embedding_provider_id : null,
    embedding_model_name: kbForm.value.embedding_source === 'byok'
      ? kbForm.value.embedding_model_name.trim() : null,
  }
  try {
    const created = await request<KnowledgeBase>('/knowledge-bases', 'POST', payload)
    kbForm.value = newKnowledgeBaseForm()
    kbModal.value = false
    await loadWorkspace()
    selectedId.value = created.id
    page.value = 'knowledgeDetail'
    knowledgeSearchHits.value = []
    await loadKnowledgeDetail()
    notify('知识库创建成功')
  } catch (error) {
    notify(error instanceof Error ? error.message : '创建知识库失败')
  }
}
function openKb(kb: KnowledgeBaseView) {
  selectedId.value = kb.id
  page.value = 'knowledgeDetail'
  knowledgeSearchHits.value = []
  void loadKnowledgeDetail()
}
async function deleteKb(kb: KnowledgeBaseView) {
  if (!window.confirm(`确定删除知识库「${kb.name}」吗？请先删除其中的全部文档。`)) return
  try {
    await request<void>(`/knowledge-bases/${kb.id}`, 'DELETE')
    await loadWorkspace()
    page.value = 'knowledge'
    notify('知识库已删除')
  } catch (error) {
    notify(error instanceof Error ? error.message : '删除知识库失败；请先删除文档')
  }
}
function upload() {
  if (!selectedId.value) return
  const input = document.createElement('input')
  input.type = 'file'
  input.multiple = true
  input.accept = '.pdf,.docx,.pptx,.xlsx,.txt,.md,.csv,.html,.htm'
  input.onchange = async () => {
    const files = Array.from(input.files ?? [])
    if (!files.length) return
    knowledgeBusy.value = true
    let succeeded = 0
    try {
      for (const file of files) {
        const form = new FormData()
        form.append('file', file)
        await apiRequest<KnowledgeDocument>(
          `/knowledge-bases/${selectedId.value}/documents/upload`,
          { method: 'POST', body: form },
        )
        succeeded += 1
      }
      await loadKnowledgeDetail()
      await loadWorkspace()
      notify(`已提交 ${succeeded} 份文档，后台正在解析和建立索引`)
    } catch (error) {
      await loadKnowledgeDetail()
      notify(error instanceof Error ? error.message : '部分文档上传失败')
    } finally {
      knowledgeBusy.value = false
    }
  }
  input.click()
}
async function importKnowledgeUrl() {
  const url = window.prompt('输入需要导入的公开网页 URL（仅支持 HTTP/HTTPS）')
  if (!url?.trim() || !selectedId.value) return
  knowledgeBusy.value = true
  try {
    await request<KnowledgeDocument>(
      `/knowledge-bases/${selectedId.value}/documents/import-url`,
      'POST',
      { url: url.trim() },
    )
    await loadKnowledgeDetail()
    notify('网页已提交，后台正在抓取和建立索引')
  } catch (error) {
    notify(error instanceof Error ? error.message : '网页导入失败')
  } finally {
    knowledgeBusy.value = false
  }
}
async function retryKnowledgeDocument(item: KnowledgeDocument) {
  try {
    await request<KnowledgeDocument>(
      `/knowledge-bases/${selectedId.value}/documents/${item.id}/retry`,
      'POST',
    )
    await loadKnowledgeDetail()
    notify('已重新提交处理任务')
  } catch (error) {
    notify(error instanceof Error ? error.message : '重试失败')
  }
}
async function reindexKnowledgeDocument(item: KnowledgeDocument) {
  try {
    await request<KnowledgeDocument>(
      `/knowledge-bases/${selectedId.value}/documents/${item.id}/reindex`,
      'POST',
    )
    await loadKnowledgeDetail()
    notify('已提交重建索引任务')
  } catch (error) {
    notify(error instanceof Error ? error.message : '重建索引失败')
  }
}
async function deleteKnowledgeDocument(item: KnowledgeDocument) {
  if (!window.confirm(`确定删除文档「${item.original_filename}」吗？`)) return
  try {
    await request<void>(
      `/knowledge-bases/${selectedId.value}/documents/${item.id}`,
      'DELETE',
    )
    await loadKnowledgeDetail()
    notify('文档已进入删除队列')
  } catch (error) {
    notify(error instanceof Error ? error.message : '删除文档失败')
  }
}
async function searchKnowledge() {
  if (!selectedId.value || !knowledgeSearchQuery.value.trim()) return
  knowledgeSearchBusy.value = true
  try {
    const result = await request<KnowledgeSearchResponse>(
      `/knowledge-bases/${selectedId.value}/search`,
      'POST',
      { query: knowledgeSearchQuery.value.trim() },
    )
    knowledgeSearchHits.value = result.hits
  } catch (error) {
    notify(error instanceof Error ? error.message : '知识库检索失败')
  } finally {
    knowledgeSearchBusy.value = false
  }
}
function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
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
    <template v-else-if="page==='knowledgeDetail'"><button class="btn secondary" @click="go('knowledge')">返回知识库</button><button class="btn secondary" @click="importKnowledgeUrl" :disabled="knowledgeBusy">导入网页</button><button class="btn primary" @click="upload" :disabled="knowledgeBusy"><Upload :size="16"/>上传文档</button></template>
    <button v-else-if="page==='api'" class="btn primary" @click="keyModal=true"><Plus :size="16"/>创建 API Key</button>
    <button v-else-if="page==='usage'" class="btn secondary" disabled><Download :size="16"/>暂无可导出数据</button>
    <button v-else-if="page==='billing'" class="btn secondary" @click="loadBilling(true)" :disabled="billingLoading"><Activity :size="16"/>刷新权益</button>
   </div></div>

   <template v-if="page==='overview'">
    <section class="hero"><div><span><Sparkles :size="14"/>让 AI 助手开始为你工作</span><h2>你好，{{currentUser?.display_name||'用户'}} <i>✦</i></h2><p>你的智能体工作空间已准备就绪。创建人格、连接知识库，开始构建专属 AI 助手。</p><button @click="go('personas')">管理我的人格 <ArrowRight :size="15"/></button></div><div class="hero-art"><div class="orbit one"></div><div class="orbit two"></div><img src="/logo.svg" alt=""><i class="orb-a"></i><i class="orb-b"></i><i class="orb-c"></i></div></section>
    <section class="stats">
     <article><div><span class="ico blue"><Bot :size="18"/></span>我的人格<button @click="go('personas')"><MoreHorizontal :size="17"/></button></div><strong>{{personas.length}}<small>个</small></strong><footer><b>{{personas.filter(p=>p.enabled).length}} 个已启用</b><span>共创建</span></footer></article>
     <article><div><span class="ico purple"><Zap :size="18"/></span>本月 Token 消耗<button @click="go('usage')"><MoreHorizontal :size="17"/></button></div><strong>—</strong><footer><span>Phase 4 接入真实计量</span></footer></article>
     <article><div><span class="ico green"><Wallet :size="18"/></span>剩余请求额度<button @click="go('billing')"><MoreHorizontal :size="17"/></button></div><strong>{{billingEntitlements?.legacy_unmetered?'不限':apiRequestQuota?.remaining_units.toLocaleString() ?? '—'}}</strong><footer><span>{{billingEntitlements?.subscription?.plan_name ?? (billingEntitlements?.legacy_unmetered?'历史 BYOK 兼容':'未开通订阅')}}</span><a @click="go('billing')">查看套餐 →</a></footer></article>
     <article><div><span class="ico amber"><Database :size="18"/></span>知识库文档<button @click="go('knowledge')"><MoreHorizontal :size="17"/></button></div><strong>{{totalDocs}}<small>份</small></strong><footer><span>{{kbs.length}} 个知识库</span><a @click="go('knowledge')">管理知识库 →</a></footer></article>
    </section>
    <section class="dashboard"><article class="panel chart-panel"><div class="panel-title"><div><b>本月 Token 用量</b><small>根据模型供应商返回的实际 usage 字段统计。</small></div><label class="running">真实计量</label></div><div class="usage-mini-grid"><div><small>输入 Token</small><b>{{usageSummary?.current_month.prompt_tokens.toLocaleString() ?? '—'}}</b></div><div><small>输出 Token</small><b>{{usageSummary?.current_month.completion_tokens.toLocaleString() ?? '—'}}</b></div><div><small>总 Token</small><b>{{usageSummary?.current_month.total_tokens.toLocaleString() ?? '—'}}</b></div></div><small class="usage-footnote">{{usageSummary?.current_month.unmetered_requests ?? 0}} 次请求暂未获得供应商 usage 统计，不计入 Token 总量。</small></article>
     <article class="panel resource"><div class="panel-title"><div><b>资源概览</b><small>当前工作空间资源使用情况</small></div><Gauge :size="18"/></div><div class="resource-line"><div><span>人格启用状态</span><b>{{personas.filter(p=>p.enabled).length}}/{{personas.length}}</b></div><i><span :style="{width:personaRatio+'%'}"></span></i><small>已启用的人格</small></div><div class="resource-line"><div><span>模型供应商</span><b>{{providers.filter(p=>p.enabled).length}}/{{providers.length}}</b></div><i><span class="violet" :style="{width:(providers.length?providers.filter(p=>p.enabled).length/providers.length*100:0)+'%'}"></span></i><small>已启用的模型供应商</small></div><div class="secure-note"><ShieldCheck :size="16"/>数据按用户空间隔离，其他用户无法访问你的资源。</div></article></section>
    <section class="panel"><div class="panel-title"><div><b>最近使用的人格</b><small>快速进入人格配置</small></div><a @click="go('personas')">查看全部 →</a></div><div v-if="personas.length" class="persona-shortcuts"><button v-for="p in personas" :key="p.id" @click="editPersona(p)"><span class="persona-icon" :class="p.color"><Bot :size="18"/></span><span><b>{{p.name}}</b><small>{{p.model_name}}</small></span><i :class="{off:!p.enabled}"></i><ChevronRight :size="15"/></button></div><div v-else class="empty"><b>尚未创建人格</b><small>创建人格并选择模型供应商，即可保存专属 AI 助手配置。</small><button class="btn primary" @click="editPersona()"><Plus :size="15"/>新建人格</button></div></section>
    <section class="panel"><div class="panel-title"><div><b>最近调用</b><small>最近 10 条模型网关请求</small></div><a @click="go('usage')">查看全部 →</a></div><div v-if="usageRecords.length" class="usage-list"><div v-for="record in usageRecords.slice(0,5)" :key="record.id" class="usage-row"><div><b>{{record.persona_slug}}</b><small>{{new Date(record.created_at).toLocaleString()}}</small></div><span>{{record.total_tokens?.toLocaleString() ?? '未统计'}} Token</span><label :class="record.status==='succeeded'?'running':'paused'">{{record.status==='succeeded'?'成功':record.status==='cancelled'?'已取消':'失败'}}</label></div></div><div v-else class="empty"><Activity :size="26"/><b>暂时没有调用记录</b><small>创建 API Key 并通过 /v1/chat/completions 发起一次请求后，这里会显示真实用量。</small></div></section>
   </template>

   <template v-else-if="page==='personas'">
    <section class="metric-strip"><div><small>全部人格</small><b>{{personas.length}}</b></div><div><small>运行中</small><b class="green-text">{{personas.filter(p=>p.enabled).length}}</b></div><div><small>已停用</small><b>{{personas.filter(p=>!p.enabled).length}}</b></div><div><small>调用方式</small><b class="small-metric">用户级 API Key</b></div></section>
    <section class="panel no-pad"><div class="toolbar"><label class="search"><Search :size="16"/><input v-model="query" placeholder="搜索人格名称或调用标识..."></label><button class="btn secondary small" @click="notify('筛选功能将在正式版开放')"><Filter :size="14"/>筛选</button></div><div v-if="filteredPersonas.length"><article v-for="p in filteredPersonas" :key="p.id" class="persona-row"><span class="persona-icon large" :class="p.color"><Bot :size="21"/></span><div class="persona-detail"><div><b>{{p.name}}</b><label :class="p.enabled?'running':'paused'"><i></i>{{p.enabled?'已启用':'已停用'}}</label></div><p>{{p.description}}</p><small><code>{{p.slug}}</code><span><Cpu :size="12"/> {{p.model_name}}</span></small></div><div class="row-actions"><button @click="togglePersona(p)" :aria-label="p.enabled?'停用':'启用'"><Zap :size="16"/></button><button @click="editPersona(p)" aria-label="编辑"><Pencil :size="16"/></button><button @click="deletePersona(p)" aria-label="删除"><Trash2 :size="16"/></button></div></article></div><div v-else class="empty"><Search :size="28"/><b>没有找到匹配的人格</b><small>试试其他关键词，或创建一个新人格。</small></div></section>
    <div class="info"><AlertCircle :size="17"/><div><b>关于人格调用</b><small>所有人格共用当前用户的 API Key。第三方请求时，将人格调用标识填写在 model 字段中，例如 customer-support。</small></div></div>
   </template>

   <template v-else-if="page==='providers'">
    <section class="metric-strip"><div><small>供应商总数</small><b>{{providers.length}}</b></div><div><small>已启用</small><b class="green-text">{{providers.filter(p=>p.enabled).length}}</b></div><div><small>已保存密钥</small><b>{{providers.filter(p=>p.has_api_key).length}}</b></div><div><small>凭据安全</small><b class="small-metric">服务端加密</b></div></section>
    <section v-if="providers.length" class="kb-grid">
     <article v-for="provider in providers" :key="provider.id" class="kb-card">
      <div class="kb-card-head"><span class="kb-icon blue"><Server :size="20"/></span><label :class="provider.enabled?'running':'paused'">{{provider.enabled?'已启用':'已停用'}}</label></div>
      <h3>{{provider.name}}</h3><p>{{provider.provider_type}} · {{provider.base_url}}</p>
      <div class="kb-meta"><span><KeyRound :size="13"/> {{provider.api_key_hint||'未配置密钥'}}</span></div>
      <footer><small><i></i>{{provider.has_api_key?'凭据已加密保存':'未设置 API Key'}}</small><div class="row-actions"><button @click="toggleProvider(provider)" :aria-label="provider.enabled?'停用':'启用'"><Zap :size="15"/></button><button @click="editProvider(provider)" aria-label="编辑"><Pencil :size="15"/></button><button @click="deleteProvider(provider)" aria-label="删除"><Trash2 :size="15"/></button></div></footer>
     </article>
    </section>
    <section v-else class="panel empty"><Server :size="30"/><b>还没有配置模型供应商</b><small>添加 DeepSeek、OpenAI、通义千问或任意 OpenAI 兼容服务。</small><button class="btn primary" @click="editProvider()"><Plus :size="15"/>添加第一个供应商</button></section>
    <div class="info"><ShieldCheck :size="17"/><div><b>凭据安全</b><small>模型供应商 API Key 使用 Fernet 对称加密后存入数据库；接口只返回密钥末四位提示，不返回密文或明文。生产环境必须配置独立的 SUPERNOVA_ENCRYPTION_KEY。</small></div></div>
   </template>

   <template v-else-if="page==='knowledge'">
    <section class="kb-summary">
     <div><Database :size="18"/><small>知识库总数</small><b>{{kbs.length}} 个</b></div>
     <div><FileText :size="18"/><small>文档总数</small><b>{{totalDocs}} 份</b></div>
     <div><CheckCircle2 :size="18"/><small>已配置 Embedding</small><b>{{kbs.filter(kb => kb.embedding_source === 'byok' ? !!kb.embedding_provider_id : !!kb.platform_embedding_model_id).length}} 个</b></div>
    </section>
    <section class="kb-grid">
     <article v-for="k in kbs" :key="k.id" class="kb-card">
      <div class="kb-card-head"><span class="kb-icon" :class="k.color"><Database :size="20"/></span><button @click="deleteKb(k)" aria-label="删除知识库"><Trash2 :size="17"/></button></div>
      <h3>{{k.name}}</h3><p>{{k.description||'暂无描述'}}</p>
      <div class="kb-meta"><span><FileText :size="13"/> {{k.documents_count}} 份文档</span><span>{{k.embedding_source==='platform'?'平台 Embedding':'BYOK Embedding'}}</span></div>
      <footer><small><i></i>知识库已配置</small><a @click="openKb(k)">管理知识库 →</a></footer>
     </article>
     <button class="kb-create" @click="kbModal=true"><span><Plus :size="22"/></span><b>创建知识库</b><small>配置 Embedding 与检索参数</small></button>
    </section>
    <div v-if="!embeddingModels.length" class="info"><AlertCircle :size="17"/><div><b>平台 Embedding 尚未配置</b><small>管理员需要先在 /admin/platform/embedding-models 配置 1536 维度的 Embedding 模型；也可以在创建知识库时使用支持 Embedding 的 BYOK 供应商。</small></div></div>
   </template>

   <template v-else-if="page==='knowledgeDetail'">
    <section class="panel kb-detail">
     <span class="kb-icon" :class="selectedKb.color"><Database :size="23"/></span>
     <div><h2>{{selectedKb.name}}</h2><p>{{selectedKb.description}}</p><small>{{selectedKb.embedding_source==='platform'?'平台 Embedding':'BYOK Embedding'}} · {{selectedKb.embedding_model_name || embeddingModels.find(model => model.id === selectedKb.platform_embedding_model_id)?.model_name || '未配置模型'}} · {{selectedKb.embedding_dimensions}} 维</small></div>
     <button class="btn secondary" @click="deleteKb(selectedKb)"><Trash2 :size="15"/>删除知识库</button>
    </section>
    <section class="metric-strip">
     <div><small>文档总数</small><b>{{knowledgeUsage?.document_count ?? selectedKb.documents_count}}</b></div>
     <div><small>可检索文档</small><b class="green-text">{{knowledgeUsage?.ready_documents ?? 0}}</b></div>
     <div><small>处理中 / 失败</small><b>{{knowledgeUsage?.queued_documents ?? 0}} / {{knowledgeUsage?.failed_documents ?? 0}}</b></div>
     <div><small>活动切片</small><b>{{knowledgeUsage?.active_chunks.toLocaleString() ?? '—'}}</b></div>
    </section>
    <section class="panel">
     <div class="panel-title"><div><b>文档管理</b><small>支持 PDF、DOCX、PPTX、XLSX、TXT、Markdown、CSV、HTML；单文件默认上限 25 MB。</small></div><div class="heading-actions"><button class="btn secondary small" @click="importKnowledgeUrl" :disabled="knowledgeBusy">导入网页 URL</button><button class="btn primary small" @click="upload" :disabled="knowledgeBusy"><Upload :size="14"/>上传文件</button><button class="btn secondary small" @click="loadKnowledgeDetail(true)" :disabled="knowledgeBusy"><Activity :size="14"/>刷新</button></div></div>
     <div v-if="knowledgeBusy && !knowledgeDocuments.length" class="empty"><Activity :size="25"/><b>正在读取知识库</b></div>
     <div v-else-if="knowledgeDocuments.length" class="usage-list">
      <div v-for="item in knowledgeDocuments" :key="item.id" class="usage-row">
       <div><b>{{item.original_filename}}</b><small>{{item.source_type==='url'?'网页导入':'文件上传'}} · {{formatBytes(item.file_size_bytes)}} · {{item.chunk_count}} 个切片 · {{new Date(item.created_at).toLocaleString()}}</small><small v-if="item.error_message" class="warning">{{item.error_message}}</small></div>
       <label :class="item.status==='ready'?'running':'paused'">{{item.status==='ready'?'可检索':item.status==='queued'?'排队中':item.status==='processing'?'处理中':item.status==='failed'?'失败':item.status==='deleting'?'删除中':'已删除'}}</label>
       <div class="row-actions">
        <button v-if="item.status==='failed' || item.status==='deleting'" @click="retryKnowledgeDocument(item)" aria-label="重试"><Activity :size="15"/></button>
        <button v-if="item.status==='ready'" @click="reindexKnowledgeDocument(item)" aria-label="重建索引"><Zap :size="15"/></button>
        <button v-if="item.status!=='deleted' && item.status!=='deleting'" @click="deleteKnowledgeDocument(item)" aria-label="删除文档"><Trash2 :size="15"/></button>
       </div>
      </div>
     </div>
     <div v-else class="empty"><FileText :size="28"/><b>当前知识库还没有文档</b><small>上传文件或导入公开网页，后台 Worker 会自动解析、切片并建立向量索引。</small><button class="btn primary" @click="upload"><Upload :size="15"/>上传第一份文档</button></div>
    </section>
    <section class="panel">
     <div class="panel-title"><div><b>检索调试</b><small>直接测试当前知识库的向量 + 关键词混合检索，并查看真实来源片段。</small></div></div>
     <form class="toolbar" @submit.prevent="searchKnowledge"><label class="search"><Search :size="16"/><input v-model="knowledgeSearchQuery" placeholder="输入一个问题，例如：退货政策是什么？"></label><button class="btn primary small" type="submit" :disabled="knowledgeSearchBusy || !knowledgeSearchQuery.trim()">{{knowledgeSearchBusy?'检索中…':'测试检索'}}</button></form>
     <div v-if="knowledgeSearchHits.length" class="kb-search-results">
      <article v-for="hit in knowledgeSearchHits" :key="hit.chunk_id" class="kb-search-hit">
       <div><b>{{hit.document_name}}</b><label class="running">{{Math.round(hit.score*100)}% 匹配</label></div>
       <small>{{hit.page_number ? '第 '+hit.page_number+' 页' : hit.sheet_name ? '工作表 '+hit.sheet_name : hit.slide_number ? '幻灯片 '+hit.slide_number : '切片 '+(hit.chunk_index+1)}} · 向量排名 {{hit.vector_rank ?? '—'}} · 关键词排名 {{hit.lexical_rank ?? '—'}}</small>
       <p>{{hit.snippet}}</p>
      </article>
     </div>
     <div v-else class="empty"><Search :size="24"/><b>输入问题开始检索</b><small>检索结果会显示来源文档、页码和匹配分数；没有相关资料时不会虚构引用。</small></div>
    </section>
    <section class="panel">
     <div class="panel-title"><div><b>知识库用量（仅统计，不扣费）</b><small>Embedding 和检索资源将在最后商业化阶段再纳入真实收费。</small></div><label class="paused">SHADOW · 不扣款</label></div>
     <div class="usage-mini-grid">
      <div><small>存储空间</small><b>{{formatBytes(knowledgeUsage?.storage_bytes ?? 0)}}</b></div>
      <div><small>Embedding Token</small><b>{{knowledgeUsage?.embedding_tokens.toLocaleString() ?? '—'}}</b></div>
      <div><small>Embedding 请求</small><b>{{knowledgeUsage?.embedding_requests.toLocaleString() ?? '—'}}</b></div>
      <div><small>检索次数</small><b>{{knowledgeUsage?.retrieval_requests.toLocaleString() ?? '—'}}</b></div>
     </div>
    </section>
    <section class="panel binding"><div class="panel-title"><div><b>已关联人格</b><small>Persona 关联此知识库后，Chat Completions 会自动执行 RAG 并返回引用。</small></div><a @click="go('personas')">管理人格 →</a></div><span v-for="persona in linkedPersonas" :key="persona.id"><Bot :size="14"/> {{persona.name}} <Check :size="13"/></span><div v-if="!linkedPersonas.length" class="empty"><b>尚未关联人格</b><small>编辑人格并选择此知识库即可启用自动 RAG。</small></div></section>
   </template>



   <template v-else-if="page==='api'">
    <section class="api-notice"><ShieldCheck :size="21"/><div><b>一个用户级密钥，调用多个独立人格</b><p>OpenAI 兼容网关已开放。所有人格共用当前用户的 API Key，通过请求中的 model 字段选择人格。</p></div></section>
    <section class="api-grid"><div class="api-main">
     <article class="panel"><div class="panel-title"><div><b>我的 API Key</b><small>密钥明文仅在创建或轮换时展示一次。</small></div><button class="btn primary small" @click="keyModal=true"><Plus :size="14"/>创建密钥</button></div>
      <div v-if="apiKeys.length" class="key-list">
       <div v-for="key in apiKeys" :key="key.id" class="key-item"><div><b>{{key.name}}</b><small><code>{{key.key_prefix}}••••</code> · {{new Date(key.created_at).toLocaleString()}}</small><label :class="key.status==='active'?'running':'paused'">{{key.status==='active'?'有效':key.status==='revoked'?'已撤销':'已过期'}}</label></div><div class="row-actions"><button @click="rotateKey(key)" :disabled="key.status!=='active'" aria-label="轮换密钥"><Zap :size="15"/></button><button @click="revokeKey(key)" :disabled="key.status!=='active'" aria-label="撤销密钥"><Trash2 :size="15"/></button></div></div>
      </div>
      <div v-else class="empty"><KeyRound :size="28"/><b>还没有 API Key</b><small>创建一个用户级密钥，可调用当前工作空间中已启用且配置好模型供应商的人格。</small><button class="btn primary" @click="keyModal=true"><Plus :size="15"/>创建 API Key</button></div>
     </article>
     <article class="panel"><div class="panel-title"><div><b>接口连接信息</b><small>兼容 OpenAI Chat Completions API。</small></div><label class="running">已开放</label></div><div class="endpoint"><div><small>Base URL</small><code>{{baseUrl}}</code></div><button @click="copy(baseUrl,'Base URL 已复制')"><Copy :size="15"/></button></div><div class="endpoint"><div><small>模型列表</small><code>GET /models</code></div><button @click="copy('/models')"><Copy :size="15"/></button></div><div class="endpoint"><div><small>对话补全</small><code>POST /chat/completions</code></div><button @click="copy('/chat/completions')"><Copy :size="15"/></button></div><div class="endpoint"><div><small>认证方式</small><code>Authorization: Bearer YOUR_USER_API_KEY</code></div><button @click="copy('Authorization: Bearer YOUR_USER_API_KEY')"><Copy :size="15"/></button></div><p class="warning"><AlertCircle :size="14"/>当前支持标准消息对话与 SSE 流式响应。Token 按供应商返回的 usage 记录；尚未配置套餐价格和余额扣费。</p></article>
     <article class="panel"><div class="panel-title"><div><b>调用示例</b><small>Model 填写人格调用标识，不是供应商模型名称。</small></div><a @click="copy(example,'示例代码已复制')"><Copy :size="14"/>复制代码</a></div><pre>{{example}}</pre></article>
    </div><aside class="api-side"><article class="panel"><div class="panel-title"><div><b>已启用的人格</b><small>使用同一把用户级 API Key 调用</small></div></div><div v-for="persona in personas.filter(p=>p.enabled)" :key="persona.id" class="model-row"><span class="persona-icon" :class="persona.color"><Bot :size="15"/></span><div><b>{{persona.name}}</b><small>{{persona.slug}}</small></div><i></i></div><div v-if="!personas.some(p=>p.enabled)" class="empty"><small>暂无已启用的人格</small></div><a @click="go('personas')">管理人格 →</a></article><article class="panel tips"><LifeBuoy :size="18"/><b>密钥安全提示</b><ul><li>不要将 API Key 提交到代码仓库。</li><li>生产环境使用 HTTPS 和独立密钥管理。</li><li>轮换后旧密钥立即失效。</li><li>密钥明文无法再次查看，请妥善保存。</li></ul></article></aside></section>
   </template>


   <template v-else-if="page==='billing'">
    <section class="metric-strip">
     <div><small>当前套餐</small><b class="small-metric">{{billingEntitlements?.subscription?.plan_name ?? (billingEntitlements?.legacy_unmetered?'历史 BYOK':'未开通')}}</b></div>
     <div><small>订阅状态</small><b class="small-metric">{{billingEntitlements?.subscription?.status==='active'?'有效':billingEntitlements?.subscription?.status==='trialing'?'试用':billingEntitlements?.legacy_unmetered?'兼容模式':'未订阅'}}</b></div>
     <div><small>API 请求剩余</small><b>{{billingEntitlements?.legacy_unmetered?'不限':apiRequestQuota?.remaining_units.toLocaleString() ?? '—'}}</b></div>
     <div><small>可用平台模型</small><b>{{platformModels.length}}</b></div>
    </section>
    <section class="panel">
     <div class="panel-title"><div><b>当前订阅</b><small>套餐由平台管理员分配；在线支付和自动续费将在 Phase 5D 接入。</small></div><label :class="billingEntitlements?.subscription?'running':'paused'">{{billingEntitlements?.subscription?'已分配':'未分配'}}</label></div>
     <div v-if="billingEntitlements?.subscription" class="usage-mini-grid">
      <div><small>套餐名称</small><b>{{billingEntitlements.subscription.plan_name}}</b></div>
      <div><small>订阅开始</small><b>{{new Date(billingEntitlements.subscription.current_period_start).toLocaleDateString()}}</b></div>
      <div><small>订阅结束</small><b>{{new Date(billingEntitlements.subscription.current_period_end).toLocaleDateString()}}</b></div>
     </div>
     <div v-else-if="billingEntitlements?.legacy_unmetered" class="info"><ShieldCheck :size="17"/><div><b>历史 BYOK 兼容模式</b><small>此工作空间没有 Phase 5B 计费策略，现有 BYOK 调用继续兼容；平台模型需要有效订阅。</small></div></div>
     <div v-else class="empty"><Wallet :size="28"/><b>当前工作空间尚未开通订阅</b><small>请联系平台管理员分配套餐。未开通前，新的工作空间不能调用平台模型或 BYOK 模型。</small></div>
    </section>
    <section class="panel">
     <div class="panel-title"><div><b>套餐资源额度</b><small>每次请求先预留额度，收到可信 usage 后按实际用量结算；超额仍会拒绝。</small></div></div>
     <div v-if="billingEntitlements?.resources.length" class="usage-mini-grid">
      <div v-for="item in billingEntitlements.resources" :key="item.resource_code">
       <small>{{item.resource_code}}</small>
       <b>{{item.used_units.toLocaleString()}} / {{item.included_units.toLocaleString()}}</b>
       <small>预留 {{item.reserved_units.toLocaleString()}} · 剩余 {{item.remaining_units.toLocaleString()}} {{item.unit}}</small>
      </div>
     </div>
     <div v-else class="empty"><b>暂无可展示的额度</b><small>订阅生效并配置套餐权益后，这里会显示请求次数和平台模型 Token 额度。</small></div>
    </section>
    <section class="panel">
     <div class="panel-title"><div><b>影子计价与计量状态</b><small>按当前模型销售价格版本计算的参考金额；仅用于验证账单，不会扣款。</small></div><label class="paused">SHADOW · 不扣款</label></div>
     <div class="usage-mini-grid">
      <div><small>本周期估算金额</small><b>{{billingUsage ? billingUsage.currency+' '+formatMinorAmount(billingUsage.estimated_amount_minor, billingUsage.currency, billingUsage.minor_unit_exponent) : '—'}}</b></div>
      <div><small>影子计价明细</small><b>{{billingUsage?.shadow_rated_items.toLocaleString() ?? '—'}}</b></div>
      <div><small>未知预留</small><b>{{billingUsage?.unknown_reservations.toLocaleString() ?? '—'}}</b></div>
      <div><small>待核对事件</small><b>{{billingUsage?.unrated_events.toLocaleString() ?? '—'}}</b></div>
     </div>
     <div class="usage-mini-grid">
      <div><small>平台模型输入 Token</small><b>{{billingUsage?.platform_input_tokens.toLocaleString() ?? '—'}}</b></div>
      <div><small>平台模型输出 Token</small><b>{{billingUsage?.platform_output_tokens.toLocaleString() ?? '—'}}</b></div>
      <div><small>BYOK 输入 Token</small><b>{{billingUsage?.byok_input_tokens.toLocaleString() ?? '—'}}</b></div>
      <div><small>BYOK 输出 Token</small><b>{{billingUsage?.byok_output_tokens.toLocaleString() ?? '—'}}</b></div>
     </div>
     <small class="usage-footnote">平台模型金额根据固定价格版本计算；BYOK 模型 Token 不计平台模型费。没有可信 usage 的请求不会估算 Token 费用，未知预留会暂时占用额度，等待管理员核对。</small>
    </section>
    <section class="panel">
     <div class="panel-title"><div><b>最近影子账本</b><small>每笔影子分录必须借贷平衡；这里不是实际应收款或钱包流水。</small></div></div>
     <div v-if="billingLedger.length" class="usage-list">
      <div v-for="entry in billingLedger" :key="entry.id" class="usage-row">
       <div><b>{{entry.kind}}</b><small>{{new Date(entry.created_at).toLocaleString()}} · {{entry.id.slice(0,8)}}</small></div>
       <span>{{entry.currency}} {{formatMinorAmount(entry.postings.filter(posting=>posting.amount_minor>0).reduce((sum,posting)=>sum+posting.amount_minor,0), entry.currency)}}</span>
       <span>{{entry.postings.map(posting=>posting.amount_minor).reduce((a,b)=>a+b,0)===0?'借贷平衡':'需核对'}}</span>
       <label class="paused">SHADOW</label>
      </div>
     </div>
     <div v-else class="empty"><b>暂无影子账本分录</b><small>平台模型产生可信 Token usage 并达到最小货币精度后，会生成成对平衡的影子分录。</small></div>
    </section>
    <section class="panel">
     <div class="panel-title"><div><b>可用套餐</b><small>当前开放的套餐信息；购买、支付和升级操作将在 Phase 5D 开放。</small></div></div>
     <div v-if="billingPlans.length" class="kb-grid">
      <article v-for="plan in billingPlans" :key="plan.id" class="kb-card">
       <div class="kb-card-head"><span class="kb-icon purple"><Wallet :size="20"/></span><label class="running">{{plan.billing_interval==='month'?'月付':'年付'}}</label></div>
       <h3>{{plan.name}}</h3><p>{{plan.code}} · v{{plan.revision}}</p>
       <div class="kb-meta"><b>{{plan.currency}} {{formatMinorAmount(plan.subscription_price_minor, plan.currency)}}</b><span>{{plan.entitlements.length}} 项权益</span></div>
       <footer><small><i></i>由平台发布</small></footer>
      </article>
     </div>
     <div v-else class="empty"><b>暂无已发布套餐</b><small>平台管理员发布套餐后，将在这里展示价格和权益。</small></div>
    </section>
   </template>

   <template v-else-if="page==='usage'">
    <section class="metric-strip"><div><small>本月请求次数</small><b>{{usageSummary?.current_month.requests.toLocaleString() ?? '—'}}</b></div><div><small>输入 Token</small><b>{{usageSummary?.current_month.prompt_tokens.toLocaleString() ?? '—'}}</b></div><div><small>输出 Token</small><b>{{usageSummary?.current_month.completion_tokens.toLocaleString() ?? '—'}}</b></div><div><small>本月总 Token</small><b class="green-text">{{usageSummary?.current_month.total_tokens.toLocaleString() ?? '—'}}</b></div></section>
    <section class="panel"><div class="panel-title"><div><b>用量统计</b><small>本月数据从 {{usageSummary ? new Date(usageSummary.month_start).toLocaleDateString() : '—'}} 起累计</small></div><button class="btn secondary small" @click="loadUsage(true)" :disabled="usageLoading"><Activity :size="14"/>刷新</button></div><div class="usage-mini-grid"><div><small>累计请求</small><b>{{usageSummary?.all_time.requests.toLocaleString() ?? '—'}}</b></div><div><small>累计 Token</small><b>{{usageSummary?.all_time.total_tokens.toLocaleString() ?? '—'}}</b></div><div><small>未返回 usage 的请求</small><b>{{usageSummary?.all_time.unmetered_requests.toLocaleString() ?? '—'}}</b></div></div><small class="usage-footnote">仅统计模型供应商明确返回的 Token 数量。未返回 usage 的请求会单独标记，不会估算或虚构 Token。当前阶段尚未启用金额扣费、充值和套餐余额。</small></section>
    <section class="panel"><div class="panel-title"><div><b>最近调用记录</b><small>记录人格、供应商模型、状态、Token 和响应时间；不保存对话正文。</small></div></div><div v-if="usageRecords.length" class="usage-list"><div v-for="record in usageRecords" :key="record.id" class="usage-row"><div><b>{{record.persona_slug}}</b><small>{{record.model_source==='platform'?'平台模型':'BYOK'}} · {{record.model_name}} · {{new Date(record.created_at).toLocaleString()}}</small></div><span>{{record.prompt_tokens?.toLocaleString() ?? '—'}} / {{record.completion_tokens?.toLocaleString() ?? '—'}}</span><span>{{record.total_tokens?.toLocaleString() ?? '未统计'}} Token</span><label :class="record.status==='succeeded'?'running':'paused'">{{record.status==='succeeded'?'成功':record.status==='cancelled'?'已取消':'失败'}}</label><small>{{record.latency_ms.toLocaleString()}} ms</small></div></div><div v-else class="empty"><Activity :size="28"/><b>暂无模型调用记录</b><small>通过 API 接口发起一次模型请求后，真实记录会显示在这里。</small></div></section>
   </template>


   <template v-else-if="page==='settings'">
    <section class="panel settings"><div class="panel-title"><div><b>基本资料</b><small>管理你的账户展示信息</small></div></div><div class="account"><span>{{(currentUser?.display_name||'U').slice(0,1).toUpperCase()}}</span><div><b>{{currentUser?.display_name}}</b><small>{{currentTenant?.name}} · {{currentTenant?.role}}</small></div></div><div class="settings-fields"><label>账户名称<input :value="currentUser?.display_name||''" readonly></label><label>登录邮箱<input :value="currentUser?.email||''" readonly></label><label>账户类型<input value="工作空间账户" readonly></label></div></section>
    <section class="panel settings"><div class="panel-title"><div><b>安全与隐私</b><small>保护你的账户和 API 资源</small></div></div><div class="setting-row"><ShieldCheck :size="18"/><div><b>登录密码</b><small>定期更新密码，保护账户安全</small></div><button class="btn secondary small" @click="notify('密码修改接口将在后续账户安全阶段接入')">修改密码</button></div><div class="setting-row"><KeyRound :size="18"/><div><b>API 密钥</b><small>管理第三方应用的访问凭证</small></div><a @click="go('api')">前往管理 →</a></div></section>
    <section class="panel settings"><div class="panel-title"><div><b>通知偏好</b><small>选择你希望接收的系统通知</small></div></div><div class="setting-row"><Bell :size="18"/><div><b>用量提醒</b><small>当 Token 额度即将耗尽时提醒我</small></div><span class="toggle on"></span></div><div class="setting-row"><MessageSquare :size="18"/><div><b>系统公告</b><small>接收产品更新和维护通知</small></div><span class="toggle"></span></div></section>
   </template>
   <footer class="page-footer"><span>© 2026 SuperNova · 智能体服务平台</span><span>● 控制平面 API 已连接</span><span>OpenAI 兼容网关与 Token 计量已接入 · 知识库文档处理与 AstrBot Worker 待接入</span></footer>
  </div>
 </main>

 <div v-if="personaModal" class="modal-backdrop" @click.self="personaModal=false"><section class="modal"><div class="modal-title"><div><small>PERSONA CONFIGURATION</small><h2>{{editingId?'编辑人格':'创建新人格'}}</h2><p>配置人格提示词、模型参数和知识库关联。</p></div><button @click="personaModal=false"><X :size="18"/></button></div>
  <div class="form-grid">
   <label>人格名称 <b>*</b><input v-model="personaForm.name" maxlength="100" placeholder="例如：智能客服"></label>
   <label>调用标识（model） <b>*</b><input v-model="personaForm.slug" maxlength="80" placeholder="例如：customer-support"><small>仅允许小写字母、数字和连字符。</small></label>
   <label class="full">人格简介<input v-model="personaForm.description" maxlength="500" placeholder="简单描述这个人格的用途"></label>
   <label class="full">模型来源<select v-model="personaForm.model_source" @change="onPersonaModelSourceChange"><option value="platform">使用 SuperNova 平台模型</option><option value="byok">自带 API Key（BYOK）</option></select><small>平台模型由 SuperNova 提供；BYOK 使用你自己配置的供应商和 API Key。</small></label>
   <label v-if="personaForm.model_source==='platform'" class="full">平台模型<select v-model="personaForm.platform_model_id"><option value="">请选择平台模型</option><option v-for="model in platformModels" :key="model.id" :value="model.id">{{model.display_name}}（{{model.model_name}}） · 输入 {{formatMinorAmount(model.input_per_million_minor, model.currency)}} / 输出 {{formatMinorAmount(model.output_per_million_minor, model.currency)}} {{model.currency}} / 百万 Token</option></select><small v-if="!platformModels.length">当前没有可用平台模型，请联系平台管理员发布模型和价格。</small></label>
   <template v-else>
    <label class="full">模型供应商<select v-model="personaForm.provider_id"><option value="">请选择供应商</option><option v-for="provider in providers" :key="provider.id" :value="provider.id" :disabled="!provider.enabled || !provider.has_api_key">{{provider.name}}（{{provider.provider_type}}）</option></select><small>BYOK 使用你自己的 API Key，Token 模型费用由供应商向你收取。</small></label>
    <label class="full">上游模型名称 <b>*</b><input v-model="personaForm.model_name" maxlength="120" placeholder="例如：deepseek-chat / gpt-4o-mini"></label>
   </template>
   <label class="full">系统提示词 <b>*</b><textarea v-model="personaForm.system_prompt" rows="6" maxlength="20000" placeholder="定义 AI 的身份、目标、表达风格和行为边界..."></textarea></label>
   <label>Temperature<input v-model.number="personaForm.temperature" type="number" min="0" max="2" step="0.1"></label>
   <label>Top P<input v-model.number="personaForm.top_p" type="number" min="0" max="1" step="0.05"></label>
   <label>最大输出 Token<input v-model.number="personaForm.max_tokens" type="number" min="1" max="65536"></label>
   <div class="form-switch"><div><b>启用人格</b><small>停用后，后续 API 网关将拒绝调用此人格。</small></div><button class="toggle" :class="{on:personaForm.enabled}" @click="personaForm.enabled=!personaForm.enabled"></button></div>
   <div class="full"><b class="form-section-title">关联知识库</b><div v-if="kbs.length" class="checkbox-grid"><label v-for="kb in kbs" :key="kb.id"><input v-model="personaForm.knowledge_base_ids" type="checkbox" :value="kb.id"><span>{{kb.name}}</span></label></div><small v-else class="form-hint">尚无知识库，可先创建知识库后再关联。</small></div>
  </div>
  <div class="modal-actions"><button class="btn secondary" @click="personaModal=false">取消</button><button class="btn primary" @click="savePersona"><Check :size="15"/>保存人格</button></div>
 </section></div>
 <div v-if="kbModal" class="modal-backdrop" @click.self="kbModal=false"><section class="modal small-modal"><div class="modal-title"><div><small>KNOWLEDGE BASE</small><h2>创建知识库</h2><p>配置资料空间、Embedding 模型和检索参数。</p></div><button @click="kbModal=false"><X :size="18"/></button></div>
  <div class="form-grid">
   <label class="full">知识库名称 <b>*</b><input v-model="kbForm.name" maxlength="100" placeholder="例如：产品资料库"></label>
   <label class="full">知识库描述<textarea v-model="kbForm.description" rows="2" maxlength="500" placeholder="说明知识库包含哪些内容..."></textarea></label>
   <label class="full">Embedding 来源<select v-model="kbForm.embedding_source"><option value="platform">使用 SuperNova 平台 Embedding</option><option value="byok">使用自己的供应商 API Key（BYOK）</option></select></label>
   <label v-if="kbForm.embedding_source==='platform'" class="full">平台 Embedding 模型<select v-model="kbForm.platform_embedding_model_id"><option value="">请选择模型</option><option v-for="model in embeddingModels" :key="model.id" :value="model.id">{{model.display_name}}（{{model.model_name}} · {{model.dimensions}} 维）</option></select><small v-if="!embeddingModels.length">管理员尚未发布可用的平台 Embedding 模型。</small></label>
   <template v-else>
    <label class="full">模型供应商<select v-model="kbForm.embedding_provider_id"><option value="">请选择已配置 API Key 的供应商</option><option v-for="provider in providers" :key="provider.id" :value="provider.id" :disabled="!provider.enabled || !provider.has_api_key || provider.provider_type==='anthropic'">{{provider.name}}（{{provider.provider_type}}）</option></select></label>
    <label class="full">Embedding 模型名称<input v-model="kbForm.embedding_model_name" maxlength="120" placeholder="例如：text-embedding-3-small"><small>供应商必须兼容 OpenAI /embeddings 接口，并返回 1536 维向量。</small></label>
   </template>
   <label>切片长度（字符）<input v-model.number="kbForm.chunk_size" type="number" min="100" max="4000"></label>
   <label>切片重叠（字符）<input v-model.number="kbForm.chunk_overlap" type="number" min="0" max="3999"></label>
   <label>检索 Top K<input v-model.number="kbForm.retrieval_top_k" type="number" min="1" max="20"></label>
   <label>相似度阈值<input v-model.number="kbForm.retrieval_score_threshold" type="number" min="0" max="1" step="0.05"></label>
   <small class="form-hint full">当前向量索引固定为 1536 维。更换 Embedding 模型需要重建知识库索引，不能混用不同向量空间。</small>
  </div>
  <div class="modal-actions"><button class="btn secondary" @click="kbModal=false">取消</button><button class="btn primary" @click="createKb"><Plus :size="15"/>创建知识库</button></div>
 </section></div>
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
 <div v-if="secretModal" class="modal-backdrop" @click.self="secretModal=false;newApiKeySecret=''"><section class="modal small-modal"><div class="modal-title"><div><small>SECRET CREATED</small><h2>请立即保存 API Key</h2><p>出于安全考虑，关闭窗口后将无法再次查看完整密钥。</p></div><button @click="secretModal=false;newApiKeySecret=''"><X :size="18"/></button></div><div class="key-field secret-field"><code>{{newApiKeySecret}}</code><button @click="copy(newApiKeySecret,'API Key 已复制')"><Copy :size="16"/></button></div><p class="warning"><AlertCircle :size="14"/>请将密钥保存在安全的密码管理器中，不要提交到代码仓库。</p><div class="modal-actions"><button class="btn secondary" @click="copy(newApiKeySecret,'API Key 已复制')"><Copy :size="15"/>复制密钥</button><button class="btn primary" @click="secretModal=false;newApiKeySecret=''">我已保存</button></div></section></div>
 <Transition name="toast"><div v-if="toast" class="toast"><CheckCircle2 :size="17"/>{{toast}}</div></Transition>
</div>
</template>
