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
  documents_count: number
  created_at: string
  updated_at: string
}

export interface Persona {
  id: string
  name: string
  slug: string
  description: string
  system_prompt: string
  provider_id: string | null
  provider_name: string | null
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
    if (typeof detail === 'string') return detail
    if (value.error && typeof value.error === 'object' && 'message' in value.error) {
      return String((value.error as Record<string, unknown>).message)
    }
  }
  return fallback
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(errorMessage(payload, \`请求失败（HTTP \${response.status}）\`))
  }
  return payload as T
}

async function rawRequest<T>(path: string, init: RequestInit, token?: string): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', \`Bearer \${token}\`)
  const response = await fetch(\`\${API_BASE_URL}\${path}\`, { ...init, headers })
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
