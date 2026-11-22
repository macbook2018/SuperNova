<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiRequest } from '../lib/api'

interface PlatformAnalytics {
  period: { days: number; start: string; end: string }
  accounts: {
    users: number
    active_users: number
    tenants: number
    active_tenants: number
    active_memberships: number
  }
  usage: {
    requests: number
    successful_requests: number
    failed_requests: number
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
  by_model_source: Array<{ model_source: string; requests: number; total_tokens: number }>
  daily: Array<{ date: string; requests: number; successful_requests: number; total_tokens: number }>
  top_models: Array<{ provider_type: string; model_name: string; requests: number; total_tokens: number }>
}
interface PlatformRole {
  code: string
  name: string
  description: string
  is_system: boolean
}
interface PlatformUser {
  id: string
  email: string
  display_name: string
  status: string
  created_at: string
  tenant_count: number | null
}
interface PlatformUserDetail extends PlatformUser {
  tenants: Array<{
    tenant_id: string
    tenant_name: string
    tenant_status: string
    membership_role: string
    membership_status: string
    joined_at: string
  }>
}
interface PlatformTenant {
  id: string
  name: string
  status: string
  created_at: string
  member_count: number | null
}
interface PlatformTenantDetail extends PlatformTenant {
  members: Array<{
    user_id: string
    email: string
    display_name: string
    user_status: string
    membership_role: string
    membership_status: string
    joined_at: string
  }>
}
interface UserRole {
  role: string
  name: string
  granted_by_user_id: string | null
  created_at: string
  expires_at: string | null
  revoked_at: string | null
  active: boolean
}
interface AuditLog {
  id: string
  actor_user_id: string | null
  target_user_id: string | null
  action: string
  resource_type: string
  resource_id: string
  result: string
  reason: string | null
  request_id: string | null
  details: Record<string, unknown>
  created_at: string
}

const props = defineProps<{
  canManage: boolean
  canAudit: boolean
  canReadUsers: boolean
  canReadTenants: boolean
  canManageTenants: boolean
  canReadAnalytics: boolean
}>()

const roles = ref<PlatformRole[]>([])
const users = ref<PlatformUser[]>([])
const userSearch = ref('')
const userTotal = ref(0)
const userOffset = ref(0)
const userPageSize = 30
const userDetail = ref<PlatformUserDetail | null>(null)
const usersLoading = ref(false)
const tenants = ref<PlatformTenant[]>([])
const tenantSearch = ref('')
const tenantTotal = ref(0)
const tenantOffset = ref(0)
const tenantPageSize = 30
const tenantDetail = ref<PlatformTenantDetail | null>(null)
const tenantsLoading = ref(false)
const tenantSaving = ref(false)
const analytics = ref<PlatformAnalytics | null>(null)
const analyticsDays = ref(30)
const analyticsLoading = ref(false)
const userId = ref('')
const userRoles = ref<UserRole[]>([])
const auditLogs = ref<AuditLog[]>([])
const selectedRole = ref('')
const reason = ref('')
const expiresAt = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const activeTab = ref<'users' | 'tenants' | 'assignments' | 'audit'>(
  props.canReadUsers ? 'users'
    : props.canReadTenants ? 'tenants'
      : props.canManage ? 'assignments' : 'audit',
)

function message(value: unknown): string {
  return value instanceof Error ? value.message : '请求失败，请稍后重试'
}
function notify(value: string) {
  notice.value = value
  window.setTimeout(() => { if (notice.value === value) notice.value = '' }, 3000)
}
async function loadAnalytics() {
  if (!props.canReadAnalytics) return
  analyticsLoading.value = true
  error.value = ''
  try {
    analytics.value = await apiRequest<PlatformAnalytics>(
      `/admin/platform/analytics/overview?days=${analyticsDays.value}`,
    )
  } catch (value) {
    error.value = message(value)
  } finally {
    analyticsLoading.value = false
  }
}
function formatNumber(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}
async function loadRoles() {
  if (!props.canManage) return
  roles.value = await apiRequest<PlatformRole[]>('/admin/platform/roles')
  if (!roles.value.some(role => role.code === selectedRole.value)) {
    selectedRole.value = roles.value[0]?.code ?? ''
  }
}
async function searchTenants() {
  if (!props.canReadTenants) return
  tenantsLoading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({
      limit: String(tenantPageSize),
      offset: String(tenantOffset.value),
    })
    if (tenantSearch.value.trim()) params.set('q', tenantSearch.value.trim())
    const result = await apiRequest<{ items: PlatformTenant[]; total: number }>(
      `/admin/platform/tenants?${params}`,
    )
    tenants.value = result.items
    tenantTotal.value = result.total
  } catch (value) {
    error.value = message(value)
    tenants.value = []
  } finally {
    tenantsLoading.value = false
  }
}
function startTenantSearch() {
  tenantOffset.value = 0
  void searchTenants()
}
async function selectTenant(tenant: PlatformTenant) {
  tenantDetail.value = null
  try {
    tenantDetail.value = await apiRequest<PlatformTenantDetail>(
      `/admin/platform/tenants/${encodeURIComponent(tenant.id)}`,
    )
  } catch (value) {
    error.value = message(value)
  }
}
async function changeTenantStatus(tenant: PlatformTenant) {
  if (!props.canManageTenants) return
  const nextStatus = tenant.status === 'active' ? 'disabled' : 'active'
  const action = nextStatus === 'disabled' ? '停用' : '恢复'
  if (!window.confirm(`确定${action}工作空间「${tenant.name}」吗？`)) return
  const reason = window.prompt(`请输入${action}原因（至少 3 个字符）`)
  if (reason === null) return
  if (reason.trim().length < 3) {
    error.value = '操作原因至少需要 3 个字符'
    return
  }
  tenantSaving.value = true
  error.value = ''
  try {
    await apiRequest(`/admin/platform/tenants/${encodeURIComponent(tenant.id)}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status: nextStatus, reason: reason.trim() }),
    })
    await searchTenants()
    if (tenantDetail.value?.id === tenant.id) await selectTenant({ ...tenant, status: nextStatus })
    notify(nextStatus === 'disabled' ? '工作空间已停用' : '工作空间已恢复')
  } catch (value) {
    error.value = message(value)
  } finally {
    tenantSaving.value = false
  }
}
async function searchUsers() {
  if (!props.canReadUsers) return
  usersLoading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ limit: String(userPageSize), offset: String(userOffset.value) })
    if (userSearch.value.trim()) params.set('q', userSearch.value.trim())
    const result = await apiRequest<{ items: PlatformUser[]; total: number }>(`/admin/platform/users?${params}`)
    users.value = result.items
    userTotal.value = result.total
  } catch (value) {
    error.value = message(value)
    users.value = []
  } finally {
    usersLoading.value = false
  }
}
function startUserSearch() {
  userOffset.value = 0
  void searchUsers()
}
async function selectUser(user: PlatformUser) {
  userId.value = user.id
  userDetail.value = null
  if (props.canReadUsers) {
    try {
      userDetail.value = await apiRequest<PlatformUserDetail>(`/admin/platform/users/${encodeURIComponent(user.id)}`)
    } catch (value) {
      error.value = message(value)
    }
  }
  if (props.canManage) {
    await loadUserRoles()
    activeTab.value = 'assignments'
  }
}
async function loadUserRoles() {
  const id = userId.value.trim()
  if (!id) {
    error.value = '请先输入用户 ID'
    return
  }
  loading.value = true
  error.value = ''
  try {
    userRoles.value = await apiRequest<UserRole[]>(`/admin/platform/users/${encodeURIComponent(id)}/roles`)
  } catch (value) {
    error.value = message(value)
    userRoles.value = []
  } finally {
    loading.value = false
  }
}
async function grantRole() {
  const id = userId.value.trim()
  if (!id || !selectedRole.value || reason.value.trim().length < 3) {
    error.value = '请填写用户 ID、选择平台角色，并填写至少 3 个字符的授权原因'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await apiRequest(`/admin/platform/users/${encodeURIComponent(id)}/roles/${encodeURIComponent(selectedRole.value)}`, {
      method: 'POST',
      body: JSON.stringify({
        reason: reason.value.trim(),
        expires_at: expiresAt.value ? new Date(expiresAt.value).toISOString() : null,
      }),
    })
    reason.value = ''
    expiresAt.value = ''
    await Promise.all([loadUserRoles(), loadAudit()])
    notify('平台角色已授予')
  } catch (value) {
    error.value = message(value)
  } finally {
    saving.value = false
  }
}
async function revokeRole(role: UserRole) {
  const id = userId.value.trim()
  const why = window.prompt(`请输入撤销「${role.name}」的原因`)
  if (why === null) return
  if (why.trim().length < 3) {
    error.value = '撤销原因至少需要 3 个字符'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await apiRequest(`/admin/platform/users/${encodeURIComponent(id)}/roles/${encodeURIComponent(role.role)}`, {
      method: 'DELETE',
      body: JSON.stringify({ reason: why.trim() }),
    })
    await Promise.all([loadUserRoles(), loadAudit()])
    notify('平台角色已撤销')
  } catch (value) {
    error.value = message(value)
  } finally {
    saving.value = false
  }
}
async function loadAudit() {
  if (!props.canAudit) return
  auditLogs.value = await apiRequest<AuditLog[]>('/admin/platform/audit-logs?limit=50')
}
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    if (props.canManage) await loadRoles()
    if (props.canReadUsers) await searchUsers()
    if (props.canReadTenants) await searchTenants()
    if (props.canReadAnalytics && activeTab.value === 'analytics') await loadAnalytics()
    if (props.canAudit && activeTab.value === 'audit') await loadAudit()
    if (userId.value.trim()) await loadUserRoles()
  } catch (value) {
    error.value = message(value)
  } finally {
    loading.value = false
  }
}
onMounted(() => { void refresh() })
</script>

<template>
  <div class="rbac-view">
    <section class="rbac-intro">
      <div><span class="eyebrow">PLATFORM SECURITY</span><h2>平台权限管理</h2><p>平台角色独立于工作空间 owner / member。授权与撤销均由后端校验并记录审计日志。</p></div>
      <button class="rbac-button secondary" :disabled="loading" @click="refresh">刷新</button>
    </section>
    <div v-if="error" class="rbac-alert error">{{ error }}</div>
    <div v-if="notice" class="rbac-alert success">{{ notice }}</div>
    <div class="rbac-tabs">
      <button v-if="canReadUsers" :class="{active:activeTab==='users'}" @click="activeTab='users';searchUsers()">平台用户</button>
      <button v-if="canReadTenants" :class="{active:activeTab==='tenants'}" @click="activeTab='tenants';searchTenants()">工作空间</button>
      <button v-if="canReadAnalytics" :class="{active:activeTab==='analytics'}" @click="activeTab='analytics';loadAnalytics()">运营统计</button>
      <button v-if="canManage" :class="{active:activeTab==='assignments'}" @click="activeTab='assignments'">角色授权</button>
      <button v-if="canAudit" :class="{active:activeTab==='audit'}" @click="activeTab='audit';loadAudit()">操作审计</button>
    </div>

    <section v-if="canReadUsers && activeTab==='users'" class="rbac-panel">
      <header><div><b>平台用户目录</b><small>按邮箱或账户名称搜索用户；仅展示账户基础信息和所属工作空间数量。</small></div><button class="rbac-button secondary" :disabled="usersLoading" @click="searchUsers">刷新</button></header>
      <div class="rbac-search"><label>搜索用户<input v-model="userSearch" placeholder="输入邮箱或账户名称" @keydown.enter="startUserSearch"></label><button class="rbac-button primary" :disabled="usersLoading" @click="startUserSearch">搜索</button></div>
      <div class="rbac-directory-meta">共 {{ userTotal }} 个用户，当前显示 {{ users.length }} 个</div>
      <div v-if="usersLoading" class="rbac-empty">正在读取用户…</div>
      <div v-else-if="!users.length" class="rbac-empty">没有找到匹配的用户</div>
      <div v-else class="rbac-table-wrap"><table class="rbac-table"><thead><tr><th>账户</th><th>状态</th><th>工作空间</th><th>注册时间</th><th>操作</th></tr></thead>
        <tbody><tr v-for="user in users" :key="user.id"><td><b>{{ user.display_name }}</b><small>{{ user.email }}</small></td><td><span :class="user.status==='active'?'rbac-on':'rbac-off'">{{ user.status==='active'?'正常':'已禁用' }}</span></td><td>{{ user.tenant_count === null ? '无权限' : user.tenant_count }}</td><td>{{ new Date(user.created_at).toLocaleString() }}</td><td><button class="rbac-link" @click="selectUser(user)">查看详情 / 授权</button></td></tr></tbody>
      </table></div>
      <div class="rbac-pagination"><button class="rbac-button secondary" :disabled="usersLoading || userOffset===0" @click="userOffset=Math.max(0,userOffset-userPageSize);searchUsers()">上一页</button><span>{{ userTotal ? Math.floor(userOffset/userPageSize)+1 : 0 }} / {{ Math.max(1,Math.ceil(userTotal/userPageSize)) }} 页</span><button class="rbac-button secondary" :disabled="usersLoading || userOffset+userPageSize>=userTotal" @click="userOffset+=userPageSize;searchUsers()">下一页</button></div>
      <div v-if="userDetail" class="rbac-selected-user"><b>{{ userDetail.display_name }}</b><span>{{ userDetail.email }}</span><small>{{ userDetail.id }}</small><div v-if="userDetail.tenants.length"><b>所属工作空间</b><span v-for="tenant in userDetail.tenants" :key="tenant.tenant_id">{{ tenant.tenant_name }} · {{ tenant.membership_role }} · {{ tenant.membership_status }}</span></div><div v-else>该用户尚未加入工作空间</div></div>
    </section>
    <section v-if="canReadTenants && activeTab==='tenants'" class="rbac-panel">
      <header><div><b>工作空间目录</b><small>查看租户状态与创建时间；成员资料需要额外的用户读取权限。</small></div><button class="rbac-button secondary" :disabled="tenantsLoading" @click="searchTenants">刷新</button></header>
      <div class="rbac-search"><label>搜索工作空间<input v-model="tenantSearch" placeholder="输入工作空间名称" @keydown.enter="startTenantSearch"></label><button class="rbac-button primary" :disabled="tenantsLoading" @click="startTenantSearch">搜索</button></div>
      <div class="rbac-directory-meta">共 {{ tenantTotal }} 个工作空间，当前显示 {{ tenants.length }} 个</div>
      <div v-if="tenantsLoading" class="rbac-empty">正在读取工作空间…</div>
      <div v-else-if="!tenants.length" class="rbac-empty">没有找到匹配的工作空间</div>
      <div v-else class="rbac-table-wrap"><table class="rbac-table"><thead><tr><th>工作空间</th><th>状态</th><th>成员数</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody><tr v-for="tenant in tenants" :key="tenant.id"><td><b>{{ tenant.name }}</b><small>{{ tenant.id }}</small></td><td><span :class="tenant.status==='active'?'rbac-on':'rbac-off'">{{ tenant.status==='active'?'正常':'已停用' }}</span></td><td>{{ tenant.member_count === null ? '无权限' : tenant.member_count }}</td><td>{{ new Date(tenant.created_at).toLocaleString() }}</td><td><button class="rbac-link" @click="selectTenant(tenant)">详情</button><button v-if="canManageTenants" class="rbac-link" :class="tenant.status==='active'?'danger':''" :disabled="tenantSaving" @click="changeTenantStatus(tenant)">{{ tenant.status==='active'?'停用':'恢复' }}</button></td></tr></tbody>
      </table></div>
      <div class="rbac-pagination"><button class="rbac-button secondary" :disabled="tenantsLoading || tenantOffset===0" @click="tenantOffset=Math.max(0,tenantOffset-tenantPageSize);searchTenants()">上一页</button><span>{{ tenantTotal ? Math.floor(tenantOffset/tenantPageSize)+1 : 0 }} / {{ Math.max(1,Math.ceil(tenantTotal/tenantPageSize)) }} 页</span><button class="rbac-button secondary" :disabled="tenantsLoading || tenantOffset+tenantPageSize>=tenantTotal" @click="tenantOffset+=tenantPageSize;searchTenants()">下一页</button></div>
      <div v-if="tenantDetail" class="rbac-selected-user"><b>{{ tenantDetail.name }}</b><span>{{ tenantDetail.id }}</span><span>状态：{{ tenantDetail.status==='active'?'正常':'已停用' }}</span><div v-if="canReadUsers"><b>工作空间成员（最多展示 100 人）</b><span v-for="member in tenantDetail.members" :key="member.user_id">{{ member.display_name }} · {{ member.email }} · {{ member.membership_role }} · {{ member.membership_status }}</span><span v-if="!tenantDetail.members.length">暂无成员记录</span></div><div v-else>当前角色没有查看成员资料的权限</div></div>
    </section>
    <section v-if="canReadAnalytics && activeTab==='analytics'" class="rbac-panel">
      <header><div><b>平台运营统计</b><small>全平台聚合数据，不展示客户邮箱、消息内容或个人明细。</small></div><select v-model.number="analyticsDays" class="rbac-range" @change="loadAnalytics"><option :value="7">最近 7 天</option><option :value="30">最近 30 天</option><option :value="90">最近 90 天</option></select></header>
      <div v-if="analyticsLoading" class="rbac-empty">正在汇总平台数据…</div>
      <template v-else-if="analytics">
        <div class="rbac-metrics">
          <div><small>注册用户</small><b>{{ formatNumber(analytics.accounts.users) }}</b><span>活跃 {{ formatNumber(analytics.accounts.active_users) }}</span></div>
          <div><small>工作空间</small><b>{{ formatNumber(analytics.accounts.tenants) }}</b><span>正常 {{ formatNumber(analytics.accounts.active_tenants) }}</span></div>
          <div><small>模型请求</small><b>{{ formatNumber(analytics.usage.requests) }}</b><span>成功 {{ formatNumber(analytics.usage.successful_requests) }} · 失败 {{ formatNumber(analytics.usage.failed_requests) }}</span></div>
          <div><small>总 Token</small><b>{{ formatNumber(analytics.usage.total_tokens) }}</b><span>输入 {{ formatNumber(analytics.usage.prompt_tokens) }} · 输出 {{ formatNumber(analytics.usage.completion_tokens) }}</span></div>
        </div>
        <div class="rbac-analytics-grid">
          <div class="rbac-analytics-block"><b>模型来源</b><table class="rbac-table"><thead><tr><th>来源</th><th>请求数</th><th>Token</th></tr></thead><tbody><tr v-for="item in analytics.by_model_source" :key="item.model_source"><td>{{ item.model_source==='platform'?'平台模型':'BYOK' }}</td><td>{{ formatNumber(item.requests) }}</td><td>{{ formatNumber(item.total_tokens) }}</td></tr></tbody></table></div>
          <div class="rbac-analytics-block"><b>热门模型 Top 10</b><table class="rbac-table"><thead><tr><th>模型</th><th>请求数</th><th>Token</th></tr></thead><tbody><tr v-for="item in analytics.top_models" :key="item.provider_type+'-'+item.model_name"><td><b>{{ item.model_name }}</b><small>{{ item.provider_type }}</small></td><td>{{ formatNumber(item.requests) }}</td><td>{{ formatNumber(item.total_tokens) }}</td></tr></tbody></table><div v-if="!analytics.top_models.length" class="rbac-empty">暂无模型调用记录</div></div>
        </div>
        <div class="rbac-analytics-block"><b>每日调用趋势</b><div class="rbac-table-wrap"><table class="rbac-table"><thead><tr><th>日期</th><th>请求数</th><th>成功数</th><th>Token</th></tr></thead><tbody><tr v-for="item in analytics.daily" :key="item.date"><td>{{ item.date }}</td><td>{{ formatNumber(item.requests) }}</td><td>{{ formatNumber(item.successful_requests) }}</td><td>{{ formatNumber(item.total_tokens) }}</td></tr></tbody></table><div v-if="!analytics.daily.length" class="rbac-empty">所选周期暂无调用记录</div></div></div>
      </template>
    </section>
    <template v-if="canManage && activeTab==='assignments'">
      <section class="rbac-panel">
        <header><div><b>用户平台角色</b><small>从平台用户目录选择用户；平台角色与工作空间角色相互独立。</small></div></header>
        <div v-if="userDetail" class="rbac-selected-user"><b>{{ userDetail.display_name }}</b><span>{{ userDetail.email }}</span><small>{{ userDetail.id }}</small><div>所属工作空间：{{ userDetail.tenants.map(item => item.tenant_name).join('、') || '无' }}</div></div>
        <div class="rbac-search">
          <label v-if="!canReadUsers">用户 ID<input v-model="userId" placeholder="粘贴用户 UUID" @keydown.enter="loadUserRoles"></label>
          <button v-if="!canReadUsers" class="rbac-button primary" :disabled="loading" @click="loadUserRoles">查询角色</button>
        </div>
        <div v-if="loading" class="rbac-empty">正在读取…</div>
        <div v-else-if="userId.trim() && !userRoles.length" class="rbac-empty">没有有效或历史平台角色记录，或尚未查询</div>
        <div v-else-if="userRoles.length" class="rbac-table-wrap">
          <table class="rbac-table"><thead><tr><th>角色</th><th>授权时间</th><th>到期时间</th><th>状态</th><th>操作</th></tr></thead>
            <tbody><tr v-for="item in userRoles" :key="`${item.role}-${item.created_at}`">
              <td><b>{{ item.name }}</b><small>{{ item.role }}</small></td>
              <td>{{ new Date(item.created_at).toLocaleString() }}</td>
              <td>{{ item.expires_at ? new Date(item.expires_at).toLocaleString() : '永久' }}</td>
              <td><span :class="item.active?'rbac-on':'rbac-off'">{{ item.active?'有效':item.revoked_at?'已撤销':'已过期' }}</span></td>
              <td><button v-if="item.active" class="rbac-link danger" :disabled="saving" @click="revokeRole(item)">撤销</button><span v-else>—</span></td>
            </tr></tbody>
          </table>
        </div>
      </section>
      <section class="rbac-panel">
        <header><div><b>授予平台角色</b><small>需要填写操作原因；可选设置到期时间。</small></div></header>
        <div class="rbac-form">
          <label>平台角色<select v-model="selectedRole"><option v-for="role in roles" :key="role.code" :value="role.code">{{ role.name }}（{{ role.code }}）</option></select></label>
          <label>到期时间（选填）<input v-model="expiresAt" type="datetime-local"></label>
          <label class="wide">授权原因<input v-model="reason" maxlength="500" placeholder="例如：负责平台模型与用户支持运营"></label>
        </div>
        <div class="rbac-role-list"><div v-for="role in roles" :key="role.code"><b>{{ role.name }}</b><code>{{ role.code }}</code><small>{{ role.description || '暂无说明' }}</small></div></div>
        <footer><button class="rbac-button primary" :disabled="!canManage || saving || !userId.trim() || !selectedRole" @click="grantRole">{{ saving?'处理中…':'授予角色' }}</button></footer>
      </section>
    </template>

    <section v-else-if="canAudit && activeTab==='audit'" class="rbac-panel">
      <header><div><b>平台管理审计日志</b><small>最近 50 条授权与平台管理操作记录。</small></div><button class="rbac-button secondary" :disabled="loading" @click="loadAudit">刷新日志</button></header>
      <div v-if="!auditLogs.length" class="rbac-empty">暂无审计记录</div>
      <div v-else class="rbac-table-wrap"><table class="rbac-table"><thead><tr><th>时间</th><th>操作</th><th>操作者</th><th>目标用户</th><th>资源</th><th>原因</th></tr></thead>
        <tbody><tr v-for="item in auditLogs" :key="item.id"><td>{{ new Date(item.created_at).toLocaleString() }}</td><td><b>{{ item.action }}</b></td><td>{{ item.actor_user_id || '系统' }}</td><td>{{ item.target_user_id || '—' }}</td><td>{{ item.resource_type }} / {{ item.resource_id }}</td><td>{{ item.reason || '—' }}</td></tr></tbody>
      </table></div>
    </section>
    <div class="rbac-security-note">安全提示：平台权限不能通过修改个人资料或工作空间角色获得。最后一个有效超级管理员受到后端保护。</div>
  </div>
</template>

<style scoped>
.rbac-view{display:flex;flex-direction:column;gap:16px;color:#34445d}
.rbac-intro{display:flex;align-items:center;justify-content:space-between;gap:14px}
.eyebrow{font-size:9px;letter-spacing:1.7px;font-weight:700;color:#91a2bc}
.rbac-intro h2{font-size:21px;margin:7px 0;color:#263751}
.rbac-intro p{font-size:11px;color:#8795aa;margin:0}
.rbac-alert{padding:11px 13px;border-radius:8px;font-size:11px}
.rbac-alert.error{background:#fff2f1;color:#b84f49;border:1px solid #ffddda}
.rbac-alert.success{background:#effaf5;color:#238c6a;border:1px solid #d8f2e6}
.rbac-tabs{display:flex;gap:6px;background:#f1f4f9;padding:4px;border-radius:9px;width:max-content}
.rbac-tabs button{border:0;background:transparent;padding:9px 14px;border-radius:7px;color:#7d8aa0;font-size:11px}
.rbac-tabs button.active{background:white;color:#3479ed;box-shadow:0 1px 4px #26364e14}
.rbac-panel{background:white;border:1px solid #e9edf4;border-radius:12px;padding:17px 18px}
.rbac-panel header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.rbac-panel header div{display:flex;flex-direction:column;gap:5px}
.rbac-panel header b{font-size:13px}
.rbac-panel header small{font-size:10px;color:#9aa5b5}
.rbac-pagination{display:flex;align-items:center;justify-content:flex-end;gap:12px;margin-top:13px;font-size:10px;color:#8795aa}.rbac-range{height:34px;border:1px solid #e5eaf2;border-radius:7px;padding:0 9px;background:white;color:#62728a;font-size:10px}.rbac-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0}.rbac-metrics>div{border:1px solid #edf0f5;border-radius:9px;padding:13px;display:flex;flex-direction:column;gap:6px}.rbac-metrics small,.rbac-metrics span{font-size:9px;color:#8c9aaf}.rbac-metrics b{font-size:22px;color:#30415a}.rbac-analytics-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:14px}.rbac-analytics-block{border:1px solid #edf0f5;border-radius:9px;padding:13px;margin-top:12px}.rbac-analytics-block>b{font-size:11px;color:#43536b}.rbac-analytics-block .rbac-table{margin-top:9px}.rbac-directory-meta{margin-top:12px;font-size:10px;color:#94a1b3}.rbac-selected-user{display:flex;flex-direction:column;gap:5px;padding:12px;margin-bottom:12px;background:#f7faff;border:1px solid #e5edfb;border-radius:8px;font-size:11px}.rbac-selected-user span,.rbac-selected-user small{color:#8492a8}.rbac-selected-user small{font-size:9px;word-break:break-all}.rbac-selected-user div{margin-top:4px;color:#667791}.rbac-search{display:flex;align-items:flex-end;gap:10px;max-width:680px}
.rbac-search label,.rbac-form label{display:flex;flex-direction:column;gap:6px;font-size:10px;color:#75839a;flex:1}
.rbac-search input,.rbac-form input,.rbac-form select{height:37px;box-sizing:border-box;border:1px solid #e7ecf3;border-radius:7px;padding:0 10px;font-size:11px;color:#43536b;background:#fbfcfe;outline:none;width:100%}
.rbac-search input:focus,.rbac-form input:focus,.rbac-form select:focus{border-color:#8db5fa;background:white}
.rbac-button{height:35px;border-radius:8px;padding:0 13px;display:inline-flex;align-items:center;justify-content:center;border:1px solid transparent;font-size:10px;font-weight:600;white-space:nowrap}
.rbac-button:disabled{opacity:.55;cursor:not-allowed}
.rbac-button.primary{background:#397cf6;color:white}
.rbac-button.secondary{background:white;border-color:#e5eaf2;color:#62728a}
.rbac-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.rbac-form label.wide{grid-column:1/-1}
.rbac-role-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px;margin-top:14px}
.rbac-role-list>div{border:1px solid #edf0f5;border-radius:8px;padding:10px;display:flex;flex-direction:column;gap:5px}
.rbac-role-list b{font-size:11px}.rbac-role-list code{font-size:9px;color:#57729b}.rbac-role-list small{font-size:9px;color:#9aa5b5}
.rbac-panel footer{display:flex;justify-content:flex-end;margin-top:15px}
.rbac-table-wrap{overflow-x:auto;margin-top:13px}
.rbac-table{width:100%;border-collapse:collapse;text-align:left;white-space:nowrap;font-size:10px}
.rbac-table th{background:#fafbfd;color:#929eaf;font-size:9px;font-weight:600;padding:10px;border-bottom:1px solid #edf0f5}
.rbac-table td{padding:11px 10px;border-bottom:1px solid #f0f2f6;color:#637188;vertical-align:middle}
.rbac-table td b,.rbac-table td small{display:block}.rbac-table td b{color:#33445d}.rbac-table td small{font-size:9px;color:#9aa5b5;margin-top:4px}
.rbac-on,.rbac-off{display:inline-block;padding:4px 8px;border-radius:20px;font-size:9px}
.rbac-on{background:#eaf9f3;color:#1c9d77}.rbac-off{background:#f1f3f6;color:#8994a5}
.rbac-link{border:0;background:transparent;font-size:10px;cursor:pointer}.rbac-link.danger{color:#c45e58}
.rbac-empty{min-height:65px;display:grid;place-items:center;color:#9aa5b5;font-size:10px}
.rbac-security-note{padding:12px 14px;background:#f3f7ff;border:1px solid #e3edff;border-radius:8px;color:#7188ad;font-size:10px;line-height:1.7}
@media(max-width:760px){.rbac-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.rbac-analytics-grid{grid-template-columns:1fr}.rbac-intro{align-items:flex-start}.rbac-search{align-items:stretch;flex-direction:column}.rbac-form{grid-template-columns:1fr}.rbac-form label.wide{grid-column:auto}.rbac-panel{padding:14px}}
</style>
