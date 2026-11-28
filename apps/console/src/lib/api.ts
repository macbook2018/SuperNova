export interface User {
  id: string
  email: string
  display_name: string
  created_at: string
}

export interface Tenant {
  id: string
  name: string
  role: string
}

export interface AuthResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: User
  tenant: Tenant
}

export interface AuthSession {
  access_token: string
  refresh_token: string
  user: User
  tenant: Tenant
}

export interface ModelProvider {
  id: string
  name: string
  provider_type: 'openai_compatible' | 'openai' | 'deepseek' | 'qwen' | 'anthropic'
  base_url: string
  enabled: boolean
  has_api_key: boolean
  api_key_hint: string | null
  created_at: string
  updated_at: string
}

export interface KnowledgeBase {
  id: string
  name: string
  description: string
  embedding_source: 'platform' | 'byok'
  platform_embedding_model_id: string | null
  embedding_provider_id: string | null
  embedding_model_name: string | null
  embedding_dimensions: number
  chunk_size: number
  chunk_overlap: number
  retrieval_top_k: number
  retrieval_score_threshold: number
  enabled: boolean
  documents_count: number
  created_at: string
  updated_at: string
}

export interface PlatformEmbeddingModel {
  id: string
  code: string
  display_name: string
  model_name: string
  dimensions: number
}

export interface KnowledgeDocument {
  id: string
  knowledge_base_id: string
  original_filename: string
  source_type: 'upload' | 'url'
  source_url: string | null
  mime_type: string
  file_size_bytes: number
  sha256: string | null
  status: 'queued' | 'processing' | 'ready' | 'failed' | 'deleting' | 'deleted'
  active_index_version: number
  chunk_count: number
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface ChunkLocation {
  block_index?: number
  page_number?: number
  page_char_start?: number
  page_char_end?: number
  slide_number?: number
  sheet_name?: string
  table_index?: number
  cell_range?: string
  line_start?: number
  line_end?: number
  row_number?: number
  row_start?: number
  row_end?: number
  paragraph_index?: number
  element_index?: number
  start?: number
  end?: number
}

export interface DocumentChunkPage {
  document_id: string
  document_index_version: number
  active_release_id: string | null
  release_revision: number | null
  snapshot: string
  has_more: boolean
  items: {
    id: string
    chunk_index: number
    content: string
    heading_path: string[]
    locations: ChunkLocation[]
  }[]
}

export interface KnowledgeUsageSummary {
  active_release_id?: string | null
  document_count: number
  ready_documents: number
  queued_documents: number
  failed_documents: number
  storage_bytes: number
  active_chunks: number
  embedding_tokens: number
  embedding_requests: number
  retrieval_requests: number
}

export interface KnowledgeSearchHit {
  chunk_id: string
  document_id: string
  document_name: string
  source_type: string
  source_url: string | null
  page_number: number | null
  sheet_name: string | null
  slide_number: number | null
  chunk_index: number
  score: number
  vector_rank: number | null
  lexical_rank: number | null
  snippet: string
  metadata: Record<string, unknown>
}

export interface KnowledgeSearchResponse {
  query: string
  knowledge_base_id: string
  hits: KnowledgeSearchHit[]
}

export interface PlatformModel {
  id: string
  code: string
  display_name: string
  provider_type: string
  model_name: string
  currency: string
  input_per_million_minor: string | number
  output_per_million_minor: string | number
  price_revision: number
}

export interface BillingPlan {
  id: string
  code: string
  revision: number
  name: string
  billing_interval: 'month' | 'year'
  currency: string
  subscription_price_minor: number
  status: string
  entitlements: Array<{
    id: string
    resource_code: string
    unit: string
    included_units: number
    hard_limit_units: number | null
    overage_rate_minor_per_unit: string | number
    overage_behavior: string
  }>
  created_at: string
}

export interface BillingSubscription {
  id: string
  tenant_id: string
  plan_id: string
  plan_code: string
  plan_name: string
  status: string
  current_period_start: string
  current_period_end: string
  cancel_at_period_end: boolean
  created_at: string
}

export interface BillingEntitlements {
  legacy_unmetered: boolean
  subscription: BillingSubscription | null
  resources: Array<{
    resource_code: string
    unit: string
    included_units: number
    hard_limit_units: number | null
    used_units: number
    reserved_units: number
    remaining_units: number
    overage_behavior: string
  }>
}

export interface BillingShadowUsageSummary {
  period_start: string
  period_end: string
  currency: string
  minor_unit_exponent: number
  estimated_amount_minor: string | number
  shadow_rated_items: number
  platform_input_tokens: number
  platform_output_tokens: number
  byok_input_tokens: number
  byok_output_tokens: number
  unrated_events: number
  unknown_reservations: number
}

export interface BillingLedgerTransaction {
  id: string
  kind: string
  mode: string
  currency: string
  reference_id: string | null
  created_at: string
  postings: Array<{
    account_kind: string
    amount_minor: number
  }>
}

export interface Persona {
  id: string
  name: string
  slug: string
  description: string
  system_prompt: string
  model_source: 'platform' | 'byok'
  provider_id: string | null
  provider_name: string | null
  platform_model_id: string | null
  platform_model_name: string | null
  model_name: string
  temperature: number
  top_p: number
  max_tokens: number
  enabled: boolean
  knowledge_base_ids: string[]
  created_at: string
  updated_at: string
}

export interface ApiKey {
  id: string
  name: string
  key_prefix: string
  status: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
}

export interface ApiKeyCreated {
  api_key: ApiKey
  secret: string
  warning: string
}

const configuredBase = import.meta.env.VITE_API_BASE_URL as string | undefined
export const API_BASE_URL = (configuredBase || 'http://localhost:8001').replace(/\/+$/, '')
const SESSION_STORAGE_KEY = 'supernova.auth.session.v1'

export function getStoredSession(): AuthSession | null {
  try {
    const value = localStorage.getItem(SESSION_STORAGE_KEY)
    return value ? (JSON.parse(value) as AuthSession) : null
  } catch {
    return null
  }
}

export function storeSession(response: AuthResponse): AuthSession {
  const session: AuthSession = {
    access_token: response.access_token,
    refresh_token: response.refresh_token,
    user: response.user,
    tenant: response.tenant,
  }
  localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
  return session
}

export function clearStoredSession(): void {
  localStorage.removeItem(SESSION_STORAGE_KEY)
}

function errorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object') {
    const value = payload as Record<string, unknown>
    const detail = value.detail
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as Record<string, unknown>).message)
    }
    if (Array.isArray(detail)) {
      const fieldNames: Record<string, string> = {
        name: '人格名称',
        slug: '调用标识',
        description: '人格简介',
        system_prompt: '系统提示词',
        provider_id: '模型供应商',
        model_name: '模型名称',
        temperature: 'Temperature',
        top_p: 'Top P',
        max_tokens: '最大输出 Token',
        knowledge_base_ids: '关联知识库',
      }
      const issues = detail.map((item) => {
        if (!item || typeof item !== 'object') return ''
        const issue = item as Record<string, unknown>
        const location = Array.isArray(issue.loc) ? issue.loc : []
        const field = String(location[location.length - 1] ?? '')
        const label = fieldNames[field] ?? field
        const message = typeof issue.msg === 'string' ? issue.msg : '参数格式不正确'
        if (message.includes('String should match pattern')) {
          return `${label}只能使用小写英文字母、数字和连字符`
        }
        if (message === 'Field required') return `${label}为必填项`
        if (message.includes('Input should be less than or equal to')) {
          return `${label}超过允许的最大值`
        }
        if (message.includes('Input should be greater than or equal to')) {
          return `${label}低于允许的最小值`
        }
        return `${label}：${message}`
      }).filter(Boolean)
      if (issues.length) return `请求参数不符合要求：${issues.join('；')}`
    }
    if (typeof detail === 'string') return detail
    if (value.error && typeof value.error === 'object') {
      const error = value.error as Record<string, unknown>
      const message = 'message' in error ? String(error.message) : fallback
      const requestId = typeof error.request_id === 'string' ? error.request_id : null
      return requestId ? `${message}（请求编号：${requestId}）` : message
    }
  }
  return fallback
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(errorMessage(payload, `请求失败（HTTP ${response.status}）`))
  }
  return payload as T
}

async function rawRequest<T>(path: string, init: RequestInit, token?: string): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })
  return parseResponse<T>(response)
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const session = getStoredSession()
  const publicAuthRoute = path === '/auth/login' || path === '/auth/register'
  const token = !publicAuthRoute ? session?.access_token : undefined

  try {
    return await rawRequest<T>(path, init, token)
  } catch (error) {
    if (!(error instanceof Error) || error.message !== '请登录后再执行此操作' ||
        !session?.refresh_token || path === '/auth/refresh' || publicAuthRoute) {
      throw error
    }

    try {
      const refreshed = await rawRequest<AuthResponse>(
        '/auth/refresh',
        { method: 'POST', body: JSON.stringify({ refresh_token: session.refresh_token }) },
      )
      const nextSession = storeSession(refreshed)
      return await rawRequest<T>(path, init, nextSession.access_token)
    } catch (refreshError) {
      clearStoredSession()
      throw refreshError
    }
  }
}

export function jsonBody(value: unknown): RequestInit {
  return { method: 'POST', body: JSON.stringify(value) }
}


export interface UsageTotals {
  requests: number
  successful_requests: number
  failed_requests: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  unmetered_requests: number
}

export interface UsageSummary {
  month_start: string
  current_month: UsageTotals
  all_time: UsageTotals
}

export interface UsageRecord {
  id: string
  request_id: string
  persona_slug: string
  provider_type: string
  model_name: string
  model_source: 'platform' | 'byok'
  platform_model_id: string | null
  status: string
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  latency_ms: number
  error_code: string | null
  created_at: string
}


export async function downloadApiFile(path: string, filename: string): Promise<void> {
  const session = getStoredSession()
  const headers = new Headers()
  if (session?.access_token) headers.set('Authorization', `Bearer ${session.access_token}`)
  const response = await fetch(`${API_BASE_URL}${path}`, { method: 'GET', headers })
  if (!response.ok) {
    let message = `下载失败（HTTP ${response.status}）`
    try {
      const payload = await response.json() as { detail?: { message?: string } }
      message = payload.detail?.message || message
    } catch {
      // Keep the HTTP fallback message.
    }
    throw new Error(message)
  }
  const blob = await response.blob()
  const objectUrl = window.URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.URL.revokeObjectURL(objectUrl)
}


export interface ChatSource {
  id: string
  source_account_id: string
  platform: string
  name: string
  export_tool: string | null
  target_sender_ref: string | null
  target_sender_name: string | null
  identity_confirmed_at: string | null
  status: string
  created_at: string
  updated_at: string
}
export interface ChatImportJob {
  id: string
  source_account_id: string
  status: string
  progress: number
  total_files: number
  processed_files: number
  parsed_text_messages: number
  ignored_non_text_messages: number
  unique_senders: number
  error_count: number
  error_summary: string | null
  attempts: number
  created_at: string
  updated_at: string
}
export interface ChatImportAccepted {
  job: ChatImportJob
  accepted_files: number
  skipped_duplicate_files: number
}
export interface ChatImportSenderPreview {
  sender_ref: string
  sender_name: string
  message_count: number
}
export interface ChatImportPreview {
  source_account_id: string
  identity_confirmed: boolean
  target_sender_ref: string | null
  senders: ChatImportSenderPreview[]
}

export interface ChatAnalysisJob {
  id: string
  import_job_id: string
  source_account_id: string
  model_source: 'platform' | 'byok'
  model_name: string
  status: 'queued' | 'running' | 'retry_wait' | 'succeeded' | 'failed' | 'cancelled'
  attempts: number
  owner_message_count: number
  faq_candidate_count: number
  input_tokens: number
  output_tokens: number
  request_count: number
  last_error: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface PersonaDraft {
  id: string
  source_account_id: string
  analysis_job_id: string
  status: 'pending_review' | 'approved' | 'rejected' | 'applied'
  summary: string
  profile_json: Record<string, unknown>
  system_prompt_suggestion: string
  evidence_message_count: number
  applied_persona_id: string | null
  approved_at: string | null
  applied_at: string | null
  created_at: string
  updated_at: string
}

export interface FaqCandidate {
  id: string
  source_account_id: string
  analysis_job_id: string
  status: 'pending_review' | 'approved' | 'rejected' | 'published'
  title: string
  category: string
  canonical_question: string
  question_variants: string[]
  answer: string
  confidence: number
  occurrence_count: number
  effective_date: string | null
  published_document_id: string | null
  approved_at: string | null
  published_at: string | null
  created_at: string
  updated_at: string
}

export interface ChatAnalysisBundle {
  analysis_job: ChatAnalysisJob | null
  persona_draft: PersonaDraft | null
  faq_candidates: FaqCandidate[]
}
