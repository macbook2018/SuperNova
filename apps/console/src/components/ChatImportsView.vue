<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Activity, AlertCircle, Check, CheckCircle2, Database, FileText, MessageSquare, Plus, RefreshCw, RotateCcw, Trash2, Upload, UserRound, Users, X } from 'lucide-vue-next'
import { apiRequest, type ChatImportAccepted, type ChatImportJob, type ChatImportJobDetail, type ChatImportPreview, type ChatMessage, type ChatSource } from '../lib/api'

const emit = defineEmits<{ notify: [message: string] }>()
const notify = (message: string) => emit('notify', message)
function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body !== undefined) init.body = JSON.stringify(body)
  return apiRequest<T>(path, init)
}
const sources = ref<ChatSource[]>([])
const sourceId = ref('')
const source = computed(() => sources.value.find(item => item.id === sourceId.value) ?? null)
const jobs = ref<ChatImportJob[]>([])
const preview = ref<ChatImportPreview | null>(null)
const messages = ref<ChatMessage[]>([])
const selectedJob = ref<ChatImportJobDetail | null>(null)
const targetRef = ref('')
const targetName = ref('')
const sourceModal = ref(false)
const sourceForm = ref({ name: '', export_tool: 'WeChat' })
const sourceBusy = ref(false)
const uploadBusy = ref(false)
const identityBusy = ref(false)
const deleteBusy = ref(false)
const loading = ref(false)
const jobsLoading = ref(false)
const messagesLoading = ref(false)
const messageOffset = ref(0)
const hasMore = ref(false)
let pollTimer: number | undefined
let pollBusy = false

const activeJobs = computed(() => jobs.value.filter(job => ['queued', 'processing'].includes(job.status)).length)
const importedCount = computed(() => jobs.value.reduce((sum, job) => sum + job.imported_messages, 0))
function dateText(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}
function bytesText(value: number): string {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 3)
  return (value / 1024 ** index).toFixed(index ? 1 : 0) + ' ' + units[index]
}
function jobLabel(status: string): string {
  const labels: Record<string, string> = { queued: '排队中', processing: '处理中', awaiting_identity: '待确认本人', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
  return labels[status] ?? status
}
function stateClass(status: string): string {
  if (['active', 'succeeded', 'parsed'].includes(status)) return 'running'
  if (['failed', 'cancelled', 'deleted'].includes(status)) return 'paused'
  return 'chat-status-pending'
}
function roleLabel(role: string): string {
  return role === 'target' ? '本人' : role === 'other' ? '其他联系人' : '待确认'
}
function roleClass(role: string): string {
  return role === 'target' ? 'running' : role === 'other' ? 'chat-status-neutral' : 'chat-status-pending'
}

async function loadJobs(showError = false) {
  if (!sourceId.value || jobsLoading.value) return
  jobsLoading.value = true
  try { jobs.value = await request<ChatImportJob[]>('/chat-sources/' + sourceId.value + '/imports') }
  catch (error) { if (showError) notify(error instanceof Error ? error.message : '读取导入任务失败') }
  finally { jobsLoading.value = false }
}
async function loadPreview(showError = false) {
  if (!sourceId.value) { preview.value = null; return }
  try {
    preview.value = await request<ChatImportPreview>('/chat-sources/' + sourceId.value + '/preview')
    targetRef.value = preview.value.target_sender_ref ?? targetRef.value
    if (preview.value.target_sender_ref) {
      const found = preview.value.participants.find(item => item.sender_ref === preview.value?.target_sender_ref)
      const currentSource = sources.value.find(item => item.id === sourceId.value)
      if (found) targetName.value = currentSource?.target_sender_name || found.sender_name
    }
  } catch (error) { if (showError) notify(error instanceof Error ? error.message : '读取联系人预览失败') }
}
async function loadMessages(reset = true, showError = false) {
  if (!sourceId.value || messagesLoading.value) return
  messagesLoading.value = true
  const offset = reset ? 0 : messageOffset.value
  try {
    const rows = await request<ChatMessage[]>('/chat-sources/' + sourceId.value + '/messages?limit=50&offset=' + offset)
    messages.value = reset ? rows : messages.value.concat(rows)
    messageOffset.value = offset + rows.length
    hasMore.value = rows.length === 50
  } catch (error) { if (showError) notify(error instanceof Error ? error.message : '读取聊天消息失败') }
  finally { messagesLoading.value = false }
}
async function loadSelected() {
  selectedJob.value = null; preview.value = null; messages.value = []; messageOffset.value = 0
  targetRef.value = ''; targetName.value = ''
  await Promise.all([loadJobs(), loadPreview(), loadMessages(true)])
}
async function loadSources(showError = false) {
  if (loading.value) return
  loading.value = true
  try {
    sources.value = await request<ChatSource[]>('/chat-sources')
    if (!sources.value.some(item => item.id === sourceId.value)) sourceId.value = sources.value[0]?.id ?? ''
    if (sourceId.value) await loadSelected()
    else { jobs.value = []; preview.value = null; messages.value = [] }
  } catch (error) { if (showError) notify(error instanceof Error ? error.message : '读取聊天数据源失败') }
  finally { loading.value = false }
}
async function chooseSource(id: string) {
  if (sourceId.value === id) return
  sourceId.value = id
  await loadSelected()
}
async function refreshAll() {
  await loadSources(true)
  if (sourceId.value) await loadJobs(true)
  notify('聊天记录数据已刷新')
}
async function createSource() {
  if (!sourceForm.value.name.trim()) { notify('请填写数据源名称'); return }
  sourceBusy.value = true
  try {
    const created = await request<ChatSource>('/chat-sources', 'POST', {
      name: sourceForm.value.name.trim(), export_tool: sourceForm.value.export_tool || null,
    })
    sources.value = [created, ...sources.value]
    sourceId.value = created.id
    sourceForm.value = { name: '', export_tool: 'WeChat' }
    sourceModal.value = false
    await loadSelected()
    notify('聊天数据源创建成功')
  } catch (error) { notify(error instanceof Error ? error.message : '创建数据源失败') }
  finally { sourceBusy.value = false }
}
function pickFiles() {
  if (!sourceId.value || uploadBusy.value) return
  const input = document.createElement('input')
  input.type = 'file'; input.multiple = true; input.accept = '.json,.csv,.txt,.html,.htm,.xlsx,.zip'
  input.onchange = () => {
    const files = Array.from(input.files ?? [])
    if (files.length) void uploadFiles(files)
  }
  input.click()
}
async function uploadFiles(files: File[]) {
  if (!sourceId.value || !files.length) return
  const form = new FormData()
  files.forEach(file => form.append('files', file, file.name))
  uploadBusy.value = true
  try {
    const accepted = await apiRequest<ChatImportAccepted>(
      '/chat-sources/' + sourceId.value + '/imports',
      { method: 'POST', headers: { 'Idempotency-Key': 'console-' + crypto.randomUUID() }, body: form },
    )
    await loadJobs(true)
    await showJob(accepted.job.id)
    notify('已接收 ' + accepted.accepted_files + ' 个文件' + (accepted.skipped_duplicate_files ? '，跳过 ' + accepted.skipped_duplicate_files + ' 个重复文件' : ''))
  } catch (error) { notify(error instanceof Error ? error.message : '上传聊天记录失败') }
  finally { uploadBusy.value = false }
}
async function showJob(id: string) {
  try { selectedJob.value = await request<ChatImportJobDetail>('/chat-imports/' + id) }
  catch (error) { notify(error instanceof Error ? error.message : '读取任务详情失败') }
}
async function retryJob(job: ChatImportJob) {
  try {
    await request('/chat-imports/' + job.id + '/retry', 'POST')
    await loadJobs(true); await showJob(job.id); notify('已重新提交失败任务')
  } catch (error) { notify(error instanceof Error ? error.message : '重试任务失败') }
}
async function cancelJob(job: ChatImportJob) {
  if (!window.confirm('确定取消这个导入任务吗？')) return
  try {
    await request('/chat-imports/' + job.id + '/cancel', 'POST')
    await loadJobs(true); await showJob(job.id); notify('导入任务已取消')
  } catch (error) { notify(error instanceof Error ? error.message : '取消任务失败') }
}
async function removeSource() {
  if (!source.value) return
  if (!window.confirm('确定删除数据源「' + source.value.name + '」及其导入任务、联系人和消息吗？此操作不可撤销。')) return
  deleteBusy.value = true
  try {
    await request('/chat-sources/' + sourceId.value, 'DELETE')
    sources.value = sources.value.filter(item => item.id !== sourceId.value)
    sourceId.value = sources.value[0]?.id ?? ''
    if (sourceId.value) await loadSelected()
    else { jobs.value = []; preview.value = null; selectedJob.value = null; messages.value = [] }
    notify('聊天数据源及其数据已删除')
  } catch (error) { notify(error instanceof Error ? error.message : '删除数据源失败') }
  finally { deleteBusy.value = false }
}
async function confirmIdentity() {
  if (!sourceId.value || !targetRef.value) { notify('请先选择本人对应的发送者'); return }
  identityBusy.value = true
  try {
    const updatedSource = await request<ChatSource>('/chat-sources/' + sourceId.value + '/confirm-identity', 'POST', {
      target_sender_ref: targetRef.value, target_sender_name: targetName.value.trim() || null,
    })
    sources.value = sources.value.map(item => item.id === updatedSource.id ? updatedSource : item)
    await Promise.all([loadJobs(true), loadPreview(true), loadMessages(true, true)])
    notify('本人身份已确认，消息身份标签已更新')
  } catch (error) { notify(error instanceof Error ? error.message : '确认本人身份失败') }
  finally { identityBusy.value = false }
}
async function poll() {
  if (pollBusy || jobsLoading.value || !sourceId.value) return
  pollBusy = true
  const old = new Map(jobs.value.map(job => [job.id, job.status]))
  try {
    const next = await request<ChatImportJob[]>('/chat-sources/' + sourceId.value + '/imports')
    jobs.value = next
    const changed = next.some(job => old.has(job.id) && old.get(job.id) !== job.status)
    if (changed) {
      await Promise.all([loadPreview(), loadMessages(true)])
      if (selectedJob.value) await showJob(selectedJob.value.id)
    }
  } catch { /* Quiet background polling; manual refresh reports errors. */ }
  finally { pollBusy = false }
}
onMounted(() => {
  void loadSources(true)
  pollTimer = window.setInterval(() => { if (sourceId.value) void poll() }, 5000)
})
onUnmounted(() => { if (pollTimer !== undefined) window.clearInterval(pollTimer) })
</script>

<template>
  <div class="chat-import-page">
    <section class="metric-strip chat-import-metrics">
      <div><small>数据源</small><b>{{ sources.length }}</b></div>
      <div><small>当前数据源任务</small><b>{{ jobs.length }}</b></div>
      <div><small>处理中任务</small><b class="green-text">{{ activeJobs }}</b></div>
      <div><small>已入库消息</small><b>{{ importedCount.toLocaleString() }}</b></div>
    </section>
    <div class="chat-import-layout">
      <aside class="panel chat-source-panel">
        <div class="panel-title"><div><b>聊天数据源</b><small>不同微信账号请分别建立数据源</small></div><button class="btn primary small" @click="sourceModal = true"><Plus :size="14" />新建</button></div>
        <div v-if="loading && !sources.length" class="empty chat-compact-empty"><Activity :size="22" /><small>正在读取数据源…</small></div>
        <div v-else-if="sources.length" class="chat-source-list">
          <button v-for="item in sources" :key="item.id" class="chat-source-item" :class="{ active: sourceId === item.id }" @click="chooseSource(item.id)">
            <span class="chat-source-icon"><MessageSquare :size="17" /></span><span class="chat-source-copy"><b>{{ item.name }}</b><small>{{ item.export_tool || '通用导出' }} · {{ new Date(item.created_at).toLocaleDateString() }}</small></span><i :class="stateClass(item.status)"></i>
          </button>
        </div>
        <div v-else class="empty chat-compact-empty"><Database :size="24" /><b>还没有聊天数据源</b><small>先创建一个数据源，再上传聊天记录。</small><button class="btn primary small" @click="sourceModal = true"><Plus :size="14" />创建数据源</button></div>
        <div class="chat-source-note"><AlertCircle :size="15" /><span>数据源之间的联系人、群聊和消息相互独立，不会跨微信账号自动合并。</span></div>
      </aside>

      <div v-if="source" class="chat-main-column">
        <section class="panel chat-source-overview">
          <div class="panel-title"><div><b>{{ source.name }}</b><small>{{ source.export_tool || '通用聊天导出' }} · 创建于 {{ dateText(source.created_at) }}</small></div><label :class="stateClass(source.status)">{{ source.status === 'active' ? '可导入' : source.status }}</label></div>
          <div class="chat-source-overview-bottom">
            <div class="chat-identity-state"><CheckCircle2 v-if="preview?.identity_confirmed" :size="16" /><UserRound v-else :size="16" /><span>{{ preview?.identity_confirmed ? '本人已确认：' + (targetName || preview?.target_sender_ref || source.target_sender_ref) : '尚未确认本人身份' }}</span></div>
            <div class="chat-toolbar-actions">
              <button class="btn secondary small" @click="refreshAll" :disabled="loading || jobsLoading"><RefreshCw :size="14" />刷新</button>
              <button class="btn primary small" @click="pickFiles" :disabled="uploadBusy || source.status !== 'active'"><Upload :size="14" />{{ uploadBusy ? '上传中…' : '导入聊天文件' }}</button>
              <button class="btn secondary small danger-outline" @click="removeSource" :disabled="deleteBusy"><Trash2 :size="14" />删除数据源</button>
            </div>
          </div>
          <div class="chat-supported-formats">支持 JSON、CSV、TXT、HTML、XLSX、ZIP；可多文件批量导入。</div>
        </section>

        <section class="panel">
          <div class="panel-title"><div><b>导入任务</b><small>后台 Worker 自动领取任务，页面每 5 秒刷新任务状态。</small></div><span class="chat-counter">{{ jobs.length }} 个任务</span></div>
          <div v-if="jobsLoading && !jobs.length" class="empty"><Activity :size="23" /><small>正在读取导入任务…</small></div>
          <div v-else-if="jobs.length" class="chat-job-list">
            <article v-for="job in jobs" :key="job.id" class="chat-job-row" :class="{ selected: selectedJob?.id === job.id }">
              <div class="chat-job-info"><div class="chat-job-title"><b>导入任务 · {{ job.id.slice(0, 8) }}</b><label :class="stateClass(job.status)">{{ jobLabel(job.status) }}</label></div>
                <small>{{ dateText(job.created_at) }} · 尝试 {{ job.attempts }} 次</small>
                <div class="chat-progress"><i :style="{ width: Math.max(0, Math.min(100, job.progress)) + '%' }"></i></div>
                <div class="chat-job-stats"><span>{{ job.processed_files }}/{{ job.total_files }} 个文件</span><span>{{ job.imported_messages.toLocaleString() }} 条入库</span><span>{{ job.duplicate_messages.toLocaleString() }} 条重复</span><span>{{ job.error_count }} 个错误</span></div>
                <p v-if="job.error_summary" class="chat-job-error">{{ job.error_summary }}</p>
              </div>
              <div class="chat-job-actions"><button class="btn secondary small" @click="showJob(job.id)">详情</button>
                <button v-if="job.status === 'failed' || job.status === 'cancelled'" class="btn secondary small" @click="retryJob(job)"><RotateCcw :size="14" />重试</button>
                <button v-if="job.status === 'queued' || job.status === 'processing'" class="btn secondary small" @click="cancelJob(job)">取消</button>
              </div>
            </article>
          </div>
          <div v-else class="empty chat-compact-empty"><FileText :size="26" /><b>还没有导入任务</b><small>上传聊天记录文件开始导入。</small><button class="btn primary small" @click="pickFiles"><Upload :size="14" />上传第一批文件</button></div>
        </section>

        <section v-if="selectedJob" class="panel">
          <div class="panel-title"><div><b>任务详情 · {{ selectedJob.id.slice(0, 8) }}</b><small>{{ jobLabel(selectedJob.status) }} · {{ dateText(selectedJob.updated_at) }}</small></div><button class="icon-action" @click="selectedJob = null" aria-label="关闭任务详情"><X :size="17" /></button></div>
          <div class="chat-file-list"><div v-for="file in selectedJob.files" :key="file.id" class="chat-file-row"><FileText :size="17" /><div><b>{{ file.original_filename }}</b><small>{{ bytesText(file.file_size_bytes) }} · {{ file.extension.toUpperCase() }} · {{ file.parsed_message_count.toLocaleString() }} 条解析</small><small v-if="file.error_message" class="chat-job-error">{{ file.error_message }}</small></div><label :class="stateClass(file.status)">{{ file.status === 'parsed' ? '已解析' : file.status === 'failed' ? '失败' : file.status === 'processing' ? '处理中' : '已上传' }}</label></div></div>
        </section>

        <div class="chat-data-grid">
          <section class="panel">
            <div class="panel-title"><div><b>本人身份确认</b><small>选择聊天记录中代表当前微信账号的发送者。</small></div><label v-if="preview?.identity_confirmed" class="running">已确认</label><label v-else class="chat-status-pending">待确认</label></div>
            <div v-if="preview?.participants.length">
              <div class="chat-participant-list">
                <button v-for="person in preview.participants" :key="person.sender_ref" class="chat-participant-row" :class="{ selected: targetRef === person.sender_ref }" @click="targetRef = person.sender_ref; targetName = person.sender_name">
                  <span class="chat-participant-avatar"><UserRound :size="16" /></span><span class="chat-participant-copy"><b>{{ person.sender_name }}</b><small>{{ person.sender_ref }}</small></span><span class="chat-participant-count">{{ person.message_count.toLocaleString() }} 条 · {{ person.conversation_count }} 个会话</span><label :class="roleClass(person.identity_role)">{{ roleLabel(person.identity_role) }}</label>
                </button>
              </div>
              <form class="chat-identity-form" @submit.prevent="confirmIdentity">
                <label>本人发送者<select v-model="targetRef" required><option value="" disabled>请选择本人对应的发送者</option><option v-for="person in preview.participants" :key="person.sender_ref" :value="person.sender_ref">{{ person.sender_name }}（{{ person.sender_ref }}）</option></select></label>
                <label>本人显示名称（选填）<input v-model="targetName" maxlength="160" placeholder="默认使用导出记录中的昵称"></label>
                <button class="btn primary small" type="submit" :disabled="identityBusy || !targetRef"><Check :size="14" />{{ identityBusy ? '保存中…' : preview.identity_confirmed ? '更新本人身份' : '确认本人身份' }}</button>
              </form>
              <small class="chat-identity-hint">确认后，本人消息标记为 target，其他参与者标记为 other。设置仅作用于当前数据源。</small>
            </div>
            <div v-else class="empty chat-compact-empty"><Users :size="25" /><b>暂无可确认的联系人</b><small>文件解析完成后，这里会显示发送者及其消息数量。</small></div>
          </section>
          <section class="panel">
            <div class="panel-title"><div><b>会话预览</b><small>按导出文件中的会话标识归集。</small></div><span class="chat-counter">{{ preview?.conversations.length ?? 0 }} 个会话</span></div>
            <div v-if="preview?.conversations.length" class="chat-conversation-list">
              <article v-for="conversation in preview.conversations" :key="conversation.id" class="chat-conversation-row"><span class="chat-conversation-icon"><Users v-if="conversation.conversation_type === 'group'" :size="16" /><MessageSquare v-else :size="16" /></span><div><b>{{ conversation.title || conversation.external_conversation_id }}</b><small>{{ conversation.conversation_type === 'group' ? '群聊' : conversation.conversation_type === 'direct' ? '单聊' : '类型待识别' }} · {{ dateText(conversation.last_message_at) }}</small></div><strong>{{ conversation.message_count.toLocaleString() }}</strong></article>
            </div>
            <div v-else class="empty chat-compact-empty"><MessageSquare :size="24" /><b>暂无会话</b><small>解析完成后显示单聊和群聊概览。</small></div>
          </section>
        </div>

        <section class="panel">
          <div class="panel-title"><div><b>聊天消息</b><small>按消息时间排序，展示当前数据源已入库的内容。</small></div><button class="btn secondary small" @click="loadMessages(true, true)" :disabled="messagesLoading"><RefreshCw :size="14" />刷新</button></div>
          <div v-if="messages.length" class="chat-message-list">
            <article v-for="message in messages" :key="message.id" class="chat-message-row"><div class="chat-message-head"><b>{{ message.sender_name || message.sender_ref }}</b><label :class="roleClass(message.identity_role)">{{ roleLabel(message.identity_role) }}</label><time>{{ dateText(message.timestamp) }}</time></div><p>{{ message.content }}</p><small>{{ message.message_type }} · {{ message.sender_ref }}</small></article>
            <button v-if="hasMore" class="btn secondary small chat-load-more" @click="loadMessages(false, true)" :disabled="messagesLoading">加载更多消息</button>
          </div>
          <div v-else class="empty chat-compact-empty"><MessageSquare :size="25" /><b>暂无已入库消息</b><small>文件解析并写入数据库后，消息会显示在这里。</small></div>
        </section>
      </div>
      <section v-else class="panel empty chat-no-source"><Database :size="34" /><b>选择或创建一个聊天数据源</b><small>每个微信登录账号建议单独创建数据源，避免联系人、群聊和聊天记录跨账号混合。</small><button class="btn primary" @click="sourceModal = true"><Plus :size="15" />创建聊天数据源</button></section>
    </div>

    <div v-if="sourceModal" class="modal-backdrop" @click.self="sourceModal = false">
      <section class="modal small-modal">
        <div class="modal-title"><div><small>CHAT DATA SOURCE</small><h2>创建聊天数据源</h2><p>一个数据源对应一个独立的聊天账号或导出集合。</p></div><button class="icon-action" @click="sourceModal = false" aria-label="关闭"><X :size="18" /></button></div>
        <form class="form-grid" @submit.prevent="createSource">
          <label class="full">数据源名称 <b>*</b><input v-model="sourceForm.name" maxlength="120" placeholder="例如：个人微信 - 主账号" required></label>
          <label class="full">导出工具<select v-model="sourceForm.export_tool"><option value="WeChat">微信通用导出</option><option value="WeKeep">WeKeep</option><option value="WeFlow">WeFlow</option><option value="WeChatMsg">WeChatMsg</option><option value="Other">其他 / 通用格式</option></select></label>
          <div class="modal-actions full"><button class="btn secondary" type="button" @click="sourceModal = false">取消</button><button class="btn primary" type="submit" :disabled="sourceBusy"><Plus :size="14" />{{ sourceBusy ? '创建中…' : '创建数据源' }}</button></div>
        </form>
      </section>
    </div>
  </div>
</template>
