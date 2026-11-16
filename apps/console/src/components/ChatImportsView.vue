<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  Activity, AlertCircle, Check, CheckCircle2, Database, FileText,
  MessageSquare, Plus, RefreshCw, Trash2, Upload, UserRound, Users, X,
} from 'lucide-vue-next'
import {
  apiRequest,
  type ChatAnalysisBundle,
  type ChatImportAccepted,
  type ChatImportJob,
  type ChatImportPreview,
  type ChatSource,
  type KnowledgeBase,
  type ModelProvider,
  type Persona,
  type PlatformModel,
} from '../lib/api'

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
const selectedSenderRef = ref('')
const loading = ref(false)
const jobsLoading = ref(false)
const sourceBusy = ref(false)
const uploadBusy = ref(false)
const identityBusy = ref(false)
const deleteBusy = ref(false)
const sourceModal = ref(false)
const sourceForm = ref({ name: '', export_tool: 'WeChat' })
const platformModels = ref<PlatformModel[]>([])
const providers = ref<ModelProvider[]>([])
const personas = ref<Persona[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])
const analysisModelSource = ref<'platform' | 'byok'>('platform')
const selectedPlatformModelId = ref('')
const selectedProviderId = ref('')
const analysisModelName = ref('')
const analysisBundle = ref<ChatAnalysisBundle | null>(null)
const selectedAnalysisJobId = ref('')
const analysisStartBusyId = ref('')
const analysisLoading = ref(false)
const personaReviewBusy = ref(false)
const selectedPersonaId = ref('')
const personaApplyMode = ref<'append' | 'replace'>('append')
const faqBusyId = ref('')
const publishKnowledgeBaseId = ref('')
let pollTimer: number | undefined
let pollBusy = false

const activeJobs = computed(() =>
  jobs.value.filter(job => ['queued', 'processing'].includes(job.status)).length,
)
const parsedTextCount = computed(() =>
  jobs.value.reduce((sum, job) => sum + job.parsed_text_messages, 0),
)
const ignoredNonTextCount = computed(() =>
  jobs.value.reduce((sum, job) => sum + job.ignored_non_text_messages, 0),
)
const selectedSender = computed(() =>
  preview.value?.senders.find(item => item.sender_ref === selectedSenderRef.value) ?? null,
)

function dateText(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

function jobLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: '排队中',
    processing: '解析中',
    awaiting_identity: '待确认本人',
    ready_for_analysis: '待提炼',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return labels[status] ?? status
}

function statusClass(status: string): string {
  if (['active', 'succeeded', 'ready_for_analysis'].includes(status)) return 'running'
  if (['failed', 'cancelled', 'deleted'].includes(status)) return 'paused'
  return 'chat-status-pending'
}

function sourceLabel(status: string): string {
  if (status === 'active') return '可导入'
  if (status === 'deleting') return '清理中'
  return status
}

async function loadAnalysisConfig() {
  try {
    const [modelData, providerData, personaData, knowledgeData] = await Promise.all([
      request<PlatformModel[]>('/platform/models'),
      request<ModelProvider[]>('/model-providers'),
      request<Persona[]>('/personas'),
      request<KnowledgeBase[]>('/knowledge-bases'),
    ])
    platformModels.value = modelData
    providers.value = providerData.filter(item => item.enabled && item.has_api_key)
    personas.value = personaData
    knowledgeBases.value = knowledgeData.filter(item => item.enabled)
    if (!selectedPlatformModelId.value) selectedPlatformModelId.value = modelData[0]?.id ?? ''
    if (!selectedProviderId.value) selectedProviderId.value = providers.value[0]?.id ?? ''
    if (!selectedPersonaId.value) selectedPersonaId.value = personaData[0]?.id ?? ''
    if (!publishKnowledgeBaseId.value) publishKnowledgeBaseId.value = knowledgeData.find(item => item.enabled)?.id ?? ''
    if (!modelData.length && providers.value.length) analysisModelSource.value = 'byok'
  } catch (error) {
    notify(error instanceof Error ? error.message : '读取分析模型配置失败')
  }
}

function analysisStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: '等待分析',
    running: '分析中',
    retry_wait: '等待重试',
    succeeded: '提炼完成',
    failed: '分析失败',
    cancelled: '已取消',
  }
  return labels[status] ?? status
}

function reviewStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending_review: '待审核',
    approved: '已批准',
    rejected: '已拒绝',
    applied: '已应用',
    published: '已发布',
  }
  return labels[status] ?? status
}

async function loadAnalysis(jobId: string, showError = false) {
  if (!jobId) return
  selectedAnalysisJobId.value = jobId
  analysisLoading.value = true
  try {
    analysisBundle.value = await request<ChatAnalysisBundle>(
      '/chat-imports/' + jobId + '/analysis',
    )
  } catch (error) {
    if (showError) notify(error instanceof Error ? error.message : '读取提炼结果失败')
  } finally {
    analysisLoading.value = false
  }
}

async function startAnalysis(job: ChatImportJob) {
  if (analysisStartBusyId.value) return
  let body: Record<string, string>
  if (analysisModelSource.value === 'platform') {
    if (!selectedPlatformModelId.value) {
      notify('请先选择平台模型')
      return
    }
    body = {
      model_source: 'platform',
      platform_model_id: selectedPlatformModelId.value,
    }
  } else {
    if (!selectedProviderId.value || !analysisModelName.value.trim()) {
      notify('请选择已配置 API Key 的供应商，并填写模型名称')
      return
    }
    body = {
      model_source: 'byok',
      provider_id: selectedProviderId.value,
      model_name: analysisModelName.value.trim(),
    }
  }
  analysisStartBusyId.value = job.id
  try {
    await request('/chat-imports/' + job.id + '/analyze', 'POST', body)
    await loadAnalysis(job.id, true)
    await loadJobs(true)
    notify('Persona 与 FAQ 提炼任务已进入队列')
  } catch (error) {
    notify(error instanceof Error ? error.message : '启动聊天提炼失败')
  } finally {
    analysisStartBusyId.value = ''
  }
}

async function savePersonaDraft() {
  const draft = analysisBundle.value?.persona_draft
  if (!draft) return
  personaReviewBusy.value = true
  try {
    await request('/persona-drafts/' + draft.id, 'PATCH', {
      summary: draft.summary,
      profile_json: draft.profile_json,
      system_prompt_suggestion: draft.system_prompt_suggestion,
    })
    await loadAnalysis(selectedAnalysisJobId.value, true)
    notify('Persona 草稿已保存，状态已回到待审核')
  } catch (error) {
    notify(error instanceof Error ? error.message : '保存 Persona 草稿失败')
  } finally {
    personaReviewBusy.value = false
  }
}

async function reviewPersona(action: 'approve' | 'reject') {
  const draft = analysisBundle.value?.persona_draft
  if (!draft) return
  personaReviewBusy.value = true
  try {
    await request('/persona-drafts/' + draft.id + '/' + action, 'POST', {})
    await loadAnalysis(selectedAnalysisJobId.value, true)
    notify(action === 'approve' ? 'Persona 草稿已批准' : 'Persona 草稿已拒绝')
  } catch (error) {
    notify(error instanceof Error ? error.message : 'Persona 审核操作失败')
  } finally {
    personaReviewBusy.value = false
  }
}

async function applyPersonaDraft() {
  const draft = analysisBundle.value?.persona_draft
  if (!draft || !selectedPersonaId.value) {
    notify('请选择要应用学习结果的 Persona')
    return
  }
  if (
    personaApplyMode.value === 'replace'
    && !window.confirm('替换会覆盖目标 Persona 当前的 system_prompt，确定继续吗？')
  ) return
  personaReviewBusy.value = true
  try {
    const latest = await request<Persona>('/personas/' + selectedPersonaId.value)
    await request('/persona-drafts/' + draft.id + '/apply', 'POST', {
      persona_id: latest.id,
      mode: personaApplyMode.value,
      expected_updated_at: latest.updated_at,
    })
    personas.value = personas.value.map(item =>
      item.id === latest.id ? { ...item, updated_at: new Date().toISOString() } : item,
    )
    await loadAnalysis(selectedAnalysisJobId.value, true)
    notify('Persona 已应用学习结果')
  } catch (error) {
    notify(error instanceof Error ? error.message : '应用 Persona 失败')
  } finally {
    personaReviewBusy.value = false
  }
}

async function saveFaqCandidate(candidate: ChatAnalysisBundle['faq_candidates'][number]) {
  faqBusyId.value = candidate.id
  try {
    await request('/faq-candidates/' + candidate.id, 'PATCH', {
      title: candidate.title,
      category: candidate.category,
      canonical_question: candidate.canonical_question,
      question_variants: candidate.question_variants,
      answer: candidate.answer,
      effective_date: candidate.effective_date || null,
    })
    await loadAnalysis(selectedAnalysisJobId.value, true)
    notify('FAQ 修改已保存，状态已回到待审核')
  } catch (error) {
    notify(error instanceof Error ? error.message : '保存 FAQ 失败')
  } finally {
    faqBusyId.value = ''
  }
}

async function reviewFaq(candidateId: string, action: 'approve' | 'reject') {
  faqBusyId.value = candidateId
  try {
    await request('/faq-candidates/' + candidateId + '/' + action, 'POST', {})
    await loadAnalysis(selectedAnalysisJobId.value, true)
    notify(action === 'approve' ? 'FAQ 已批准' : 'FAQ 已拒绝')
  } catch (error) {
    notify(error instanceof Error ? error.message : 'FAQ 审核操作失败')
  } finally {
    faqBusyId.value = ''
  }
}

async function publishFaq(candidateId: string) {
  if (!publishKnowledgeBaseId.value) {
    notify('请先选择目标知识库')
    return
  }
  faqBusyId.value = candidateId
  try {
    await request('/faq-candidates/' + candidateId + '/publish', 'POST', {
      knowledge_base_id: publishKnowledgeBaseId.value,
    })
    await loadAnalysis(selectedAnalysisJobId.value, true)
    notify('FAQ 已提交到知识库，后台正在建立索引')
  } catch (error) {
    notify(error instanceof Error ? error.message : '发布 FAQ 失败')
  } finally {
    faqBusyId.value = ''
  }
}

async function loadJobs(showError = false) {
  if (!sourceId.value || jobsLoading.value) return
  jobsLoading.value = true
  try {
    jobs.value = await request<ChatImportJob[]>(
      '/chat-sources/' + sourceId.value + '/imports',
    )
  } catch (error) {
    if (showError) notify(error instanceof Error ? error.message : '读取导入任务失败')
  } finally {
    jobsLoading.value = false
  }
}

async function loadPreview(showError = false) {
  if (!sourceId.value) {
    preview.value = null
    selectedSenderRef.value = ''
    return
  }
  try {
    preview.value = await request<ChatImportPreview>(
      '/chat-sources/' + sourceId.value + '/preview',
    )
    if (preview.value.identity_confirmed) selectedSenderRef.value = ''
  } catch (error) {
    if (showError) notify(error instanceof Error ? error.message : '读取发送者摘要失败')
  }
}

async function loadSelected() {
  preview.value = null
  jobs.value = []
  analysisBundle.value = null
  selectedAnalysisJobId.value = ''
  selectedSenderRef.value = ''
  await Promise.all([loadJobs(), loadPreview()])
}

async function loadSources(showError = false) {
  if (loading.value) return
  loading.value = true
  try {
    sources.value = await request<ChatSource[]>('/chat-sources')
    if (!sources.value.some(item => item.id === sourceId.value)) {
      sourceId.value = sources.value[0]?.id ?? ''
    }
    if (sourceId.value) await loadSelected()
    else {
      jobs.value = []
      preview.value = null
    }
  } catch (error) {
    if (showError) notify(error instanceof Error ? error.message : '读取来源账号失败')
  } finally {
    loading.value = false
  }
}

async function chooseSource(id: string) {
  if (sourceId.value === id) return
  sourceId.value = id
  await loadSelected()
}

async function createSource() {
  if (!sourceForm.value.name.trim()) {
    notify('请填写来源账号名称')
    return
  }
  sourceBusy.value = true
  try {
    const created = await request<ChatSource>('/chat-sources', 'POST', {
      name: sourceForm.value.name.trim(),
      platform: 'wechat',
      export_tool: sourceForm.value.export_tool || null,
    })
    sources.value = [created, ...sources.value]
    sourceId.value = created.id
    sourceForm.value = { name: '', export_tool: 'WeChat' }
    sourceModal.value = false
    await loadSelected()
    notify('独立来源账号已创建')
  } catch (error) {
    notify(error instanceof Error ? error.message : '创建来源账号失败')
  } finally {
    sourceBusy.value = false
  }
}

function pickFiles() {
  if (!sourceId.value || uploadBusy.value) return
  const input = document.createElement('input')
  input.type = 'file'
  input.multiple = true
  input.accept = '.json,.csv,.txt,.html,.htm,.xlsx,.zip'
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
      {
        method: 'POST',
        headers: { 'Idempotency-Key': 'console-' + crypto.randomUUID() },
        body: form,
      },
    )
    await loadJobs(true)
    notify(
      '已接收 ' + accepted.accepted_files + ' 个文件' +
      (accepted.skipped_duplicate_files
        ? '，跳过 ' + accepted.skipped_duplicate_files + ' 个重复文件'
        : ''),
    )
  } catch (error) {
    notify(error instanceof Error ? error.message : '上传文本导出文件失败')
  } finally {
    uploadBusy.value = false
  }
}

async function confirmIdentity() {
  if (!sourceId.value || !selectedSender.value) {
    notify('请先明确选择代表当前微信账号本人的发送者')
    return
  }
  identityBusy.value = true
  try {
    const updatedSource = await request<ChatSource>(
      '/chat-sources/' + sourceId.value + '/confirm-identity',
      'POST',
      {
        target_sender_ref: selectedSender.value.sender_ref,
        target_sender_name: selectedSender.value.sender_name,
      },
    )
    sources.value = sources.value.map(item =>
      item.id === updatedSource.id ? updatedSource : item,
    )
    await Promise.all([loadJobs(true), loadPreview(true)])
    notify('本人身份已确认，导入任务已进入待提炼阶段')
  } catch (error) {
    notify(error instanceof Error ? error.message : '确认本人身份失败')
  } finally {
    identityBusy.value = false
  }
}

async function cancelJob(job: ChatImportJob) {
  if (!window.confirm('确定取消该任务并删除临时上传文件吗？')) return
  try {
    await request('/chat-imports/' + job.id + '/cancel', 'POST')
    await loadJobs(true)
    notify('任务已取消，临时文件已请求清理')
  } catch (error) {
    notify(error instanceof Error ? error.message : '取消任务失败')
  }
}

async function removeSource() {
  if (!source.value) return
  if (!window.confirm('确定删除来源账号「' + source.value.name + '」及其任务和临时文件吗？')) {
    return
  }
  deleteBusy.value = true
  try {
    await request('/chat-sources/' + sourceId.value, 'DELETE')
    sources.value = sources.value.filter(item => item.id !== sourceId.value)
    sourceId.value = sources.value[0]?.id ?? ''
    if (sourceId.value) await loadSelected()
    else {
      jobs.value = []
      preview.value = null
    }
    notify('来源账号及其导入数据已删除')
  } catch (error) {
    notify(error instanceof Error ? error.message : '删除来源账号失败')
  } finally {
    deleteBusy.value = false
  }
}

async function refreshAll() {
  await loadSources(true)
  notify('文本导入状态已刷新')
}

async function poll() {
  if (pollBusy || jobsLoading.value || !sourceId.value) return
  pollBusy = true
  const old = new Map(jobs.value.map(job => [job.id, job.status]))
  try {
    const next = await request<ChatImportJob[]>(
      '/chat-sources/' + sourceId.value + '/imports',
    )
    jobs.value = next
    if (next.some(job => old.has(job.id) && old.get(job.id) !== job.status)) {
      await loadPreview()
    }
    if (selectedAnalysisJobId.value && analysisBundle.value?.analysis_job &&
        ['queued', 'running', 'retry_wait'].includes(analysisBundle.value.analysis_job.status)) {
      await loadAnalysis(selectedAnalysisJobId.value)
    }
  } catch {
    // Background polling stays quiet; manual refresh reports errors.
  } finally {
    pollBusy = false
  }
}

onMounted(() => {
  void loadAnalysisConfig()
  void loadSources(true)
  pollTimer = window.setInterval(() => {
    if (sourceId.value) void poll()
  }, 5000)
})
onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer)
})
</script>

<template>
  <div class="chat-import-page">
    <section class="metric-strip chat-import-metrics">
      <div><small>独立来源账号</small><b>{{ sources.length }}</b></div>
      <div><small>导入任务</small><b>{{ jobs.length }}</b></div>
      <div><small>文本消息</small><b>{{ parsedTextCount.toLocaleString() }}</b></div>
      <div><small>跳过的非文本消息</small><b>{{ ignoredNonTextCount.toLocaleString() }}</b></div>
    </section>

    <div class="chat-import-layout">
      <aside class="panel chat-source-panel">
        <div class="panel-title">
          <div><b>微信来源账号</b><small>每个登录账号单独建立</small></div>
          <button class="btn primary small" @click="sourceModal = true"><Plus :size="14" />新建</button>
        </div>
        <div v-if="loading && !sources.length" class="empty chat-compact-empty">
          <Activity :size="22" /><small>正在读取来源账号…</small>
        </div>
        <div v-else-if="sources.length" class="chat-source-list">
          <button
            v-for="item in sources"
            :key="item.id"
            class="chat-source-item"
            :class="{ active: sourceId === item.id }"
            @click="chooseSource(item.id)"
          >
            <span class="chat-source-icon"><MessageSquare :size="17" /></span>
            <span class="chat-source-copy">
              <b>{{ item.name }}</b>
              <small>{{ item.export_tool || '通用导出' }} · {{ item.source_account_id.slice(0, 8) }}</small>
            </span>
            <i :class="statusClass(item.status)"></i>
          </button>
        </div>
        <div v-else class="empty chat-compact-empty">
          <Database :size="24" /><b>还没有来源账号</b>
          <small>先创建一个独立微信来源账号，再导入聊天文本。</small>
          <button class="btn primary small" @click="sourceModal = true"><Plus :size="14" />创建来源账号</button>
        </div>
        <div class="chat-source-note">
          <AlertCircle :size="15" />
          <span>每个来源账号由 SuperNova 独立生成 ID。即使 wxid 或昵称相同，也不会跨账号合并。</span>
        </div>
      </aside>

      <div v-if="source" class="chat-main-column">
        <section class="panel chat-source-overview">
          <div class="panel-title">
            <div>
              <b>{{ source.name }}</b>
              <small>Source Account ID：{{ source.source_account_id }}</small>
            </div>
            <label :class="statusClass(source.status)">{{ sourceLabel(source.status) }}</label>
          </div>
          <div class="chat-source-overview-bottom">
            <div class="chat-identity-state">
              <CheckCircle2 v-if="preview?.identity_confirmed" :size="16" />
              <UserRound v-else :size="16" />
              <span>
                {{ preview?.identity_confirmed
                  ? '本人身份已确认：' + (source.target_sender_name || source.target_sender_ref)
                  : '导入后需要明确选择本人发送者' }}
              </span>
            </div>
            <div class="chat-toolbar-actions">
              <button class="btn secondary small" @click="refreshAll" :disabled="loading || jobsLoading">
                <RefreshCw :size="14" />刷新
              </button>
              <button class="btn primary small" @click="pickFiles" :disabled="uploadBusy || source.status !== 'active'">
                <Upload :size="14" />{{ uploadBusy ? '上传中…' : '导入文本文件' }}
              </button>
              <button class="btn secondary small danger-outline" @click="removeSource" :disabled="deleteBusy">
                <Trash2 :size="14" />删除账号
              </button>
            </div>
          </div>
          <div class="chat-supported-formats">
            支持 JSON、CSV、TXT、HTML、XLSX、ZIP。只处理文本消息；图片、语音、视频、文件和表情等非文本内容会跳过。
            原始导入文件仅临时保留，最长 24 小时。
          </div>
        </section>

        <section class="panel">
          <div class="panel-title">
            <div><b>文本导入任务</b><small>只保存任务状态和统计，不保存聊天正文。</small></div>
            <span class="chat-counter">{{ jobs.length }} 个任务</span>
          </div>
          <div v-if="jobsLoading && !jobs.length" class="empty"><Activity :size="23" /><small>正在读取任务…</small></div>
          <div v-else-if="jobs.length" class="chat-job-list">
            <article v-for="job in jobs" :key="job.id" class="chat-job-row">
              <div class="chat-job-info">
                <div class="chat-job-title">
                  <b>任务 · {{ job.id.slice(0, 8) }}</b>
                  <label :class="statusClass(job.status)">{{ jobLabel(job.status) }}</label>
                </div>
                <small>{{ dateText(job.created_at) }} · 尝试 {{ job.attempts }} 次</small>
                <div class="chat-progress"><i :style="{ width: Math.max(0, Math.min(100, job.progress)) + '%' }"></i></div>
                <div class="chat-job-stats">
                  <span>{{ job.processed_files }}/{{ job.total_files }} 个文件</span>
                  <span>{{ job.parsed_text_messages.toLocaleString() }} 条文本</span>
                  <span>跳过 {{ job.ignored_non_text_messages.toLocaleString() }} 条非文本</span>
                  <span>{{ job.unique_senders }} 个发送者</span>
                  <span>{{ job.error_count }} 个错误</span>
                </div>
                <p v-if="job.error_summary" class="chat-job-error">{{ job.error_summary }}</p>
                <small v-if="job.status === 'ready_for_analysis'" class="chat-analysis-hint">
                  本人身份已确认，可以开始 Persona 人格学习与 FAQ 提取。
                </small>
              </div>
              <div class="chat-job-actions">
                <button
                  v-if="job.status === 'ready_for_analysis'"
                  class="btn primary small"
                  :disabled="analysisStartBusyId === job.id"
                  @click="startAnalysis(job)"
                >{{ analysisStartBusyId === job.id ? '提交中…' : '开始提炼' }}</button>
                <button
                  v-if="job.status === 'succeeded'"
                  class="btn secondary small"
                  @click="loadAnalysis(job.id, true)"
                >查看审核结果</button>
                <button
                  v-if="['queued', 'processing', 'awaiting_identity', 'ready_for_analysis'].includes(job.status)"
                  class="btn secondary small"
                  @click="cancelJob(job)"
                >取消</button>
              </div>
            </article>
          </div>
          <div v-else class="empty chat-compact-empty">
            <FileText :size="26" /><b>还没有导入任务</b>
            <small>上传聊天文本，系统会先统计发送者，确认本人身份后再进入分析阶段。</small>
            <button class="btn primary small" @click="pickFiles"><Upload :size="14" />上传文本文件</button>
          </div>
        </section>

        <section
          v-if="preview?.identity_confirmed && jobs.some(job => job.status === 'ready_for_analysis')"
          class="panel chat-analysis-settings"
        >
          <div class="panel-title">
            <div>
              <b>提炼模型设置</b>
              <small>选择用于 Persona 学习和 FAQ 提取的大模型；支持平台模型与自带 API Key。</small>
            </div>
            <span class="chat-counter">仅分析已确认本人的数据</span>
          </div>
          <div class="chat-analysis-model-grid">
            <label>
              模型来源
              <select v-model="analysisModelSource">
                <option value="platform">平台模型</option>
                <option value="byok">自带 API Key（BYOK）</option>
              </select>
            </label>
            <label v-if="analysisModelSource === 'platform'">
              平台模型
              <select v-model="selectedPlatformModelId">
                <option value="">请选择平台模型</option>
                <option v-for="item in platformModels" :key="item.id" :value="item.id">
                  {{ item.display_name }} · {{ item.model_name }}
                </option>
              </select>
            </label>
            <template v-else>
              <label>
                模型供应商
                <select v-model="selectedProviderId">
                  <option value="">请选择供应商</option>
                  <option v-for="item in providers" :key="item.id" :value="item.id">
                    {{ item.name }}
                  </option>
                </select>
              </label>
              <label>
                上游模型名称
                <input v-model="analysisModelName" maxlength="120" placeholder="例如：deepseek-chat">
              </label>
            </template>
          </div>
          <p v-if="analysisModelSource === 'platform' && !platformModels.length" class="chat-analysis-hint">
            当前没有可用的平台模型，请配置平台模型价格与凭据，或切换到 BYOK。
          </p>
          <p v-if="analysisModelSource === 'byok' && !providers.length" class="chat-analysis-hint">
            当前没有已启用且配置 API Key 的供应商，请先到「模型供应商」添加。
          </p>
        </section>

        <section v-if="selectedAnalysisJobId && analysisBundle" class="panel chat-learning-results">
          <div class="panel-title">
            <div>
              <b>Persona 与 FAQ 学习结果</b>
              <small>
                {{ analysisBundle.analysis_job
                  ? analysisStatusLabel(analysisBundle.analysis_job.status) + ' · ' + analysisBundle.analysis_job.model_name
                  : '等待分析任务' }}
              </small>
            </div>
            <button class="btn secondary small" @click="loadAnalysis(selectedAnalysisJobId, true)" :disabled="analysisLoading">
              <RefreshCw :size="14" />刷新结果
            </button>
          </div>
          <div v-if="analysisLoading" class="empty chat-compact-empty">
            <Activity :size="20" /><small>正在读取学习结果…</small>
          </div>
          <template v-else-if="analysisBundle.analysis_job">
            <div class="chat-analysis-metrics">
              <span>本人消息 {{ analysisBundle.analysis_job.owner_message_count.toLocaleString() }} 条</span>
              <span>FAQ 候选 {{ analysisBundle.analysis_job.faq_candidate_count }} 条</span>
              <span>模型请求 {{ analysisBundle.analysis_job.request_count }} 次</span>
              <span>Token {{ (analysisBundle.analysis_job.input_tokens + analysisBundle.analysis_job.output_tokens).toLocaleString() }}</span>
            </div>
            <p v-if="analysisBundle.analysis_job.last_error" class="chat-job-error">
              {{ analysisBundle.analysis_job.last_error }}
            </p>

            <section v-if="analysisBundle.persona_draft" class="chat-review-section">
              <div class="chat-review-heading">
                <div>
                  <b>6.2 Persona 人格学习草稿</b>
                  <small>{{ reviewStatusLabel(analysisBundle.persona_draft.status) }} · 基于 {{ analysisBundle.persona_draft.evidence_message_count }} 条本人消息</small>
                </div>
                <span class="chat-review-status">{{ reviewStatusLabel(analysisBundle.persona_draft.status) }}</span>
              </div>
              <p class="chat-persona-summary">{{ analysisBundle.persona_draft.summary }}</p>
              <details class="chat-profile-details">
                <summary>查看结构化表达特征</summary>
                <pre>{{ JSON.stringify(analysisBundle.persona_draft.profile_json, null, 2) }}</pre>
              </details>
              <label class="chat-review-field">
                Persona 提示词建议
                <textarea v-model="analysisBundle.persona_draft.system_prompt_suggestion" rows="7" maxlength="20000"></textarea>
              </label>
              <div class="chat-review-actions">
                <button
                  class="btn secondary small"
                  @click="savePersonaDraft"
                  :disabled="personaReviewBusy || ['rejected', 'applied'].includes(analysisBundle.persona_draft.status)"
                >保存修改</button>
                <button
                  class="btn primary small"
                  @click="reviewPersona('approve')"
                  :disabled="personaReviewBusy || analysisBundle.persona_draft.status !== 'pending_review'"
                >批准草稿</button>
                <button
                  class="btn secondary small danger-outline"
                  @click="reviewPersona('reject')"
                  :disabled="personaReviewBusy || !['pending_review', 'approved'].includes(analysisBundle.persona_draft.status)"
                >拒绝</button>
              </div>
              <div v-if="analysisBundle.persona_draft.status === 'approved'" class="chat-persona-apply">
                <label>
                  应用到现有 Persona
                  <select v-model="selectedPersonaId">
                    <option value="">请选择目标 Persona</option>
                    <option v-for="item in personas" :key="item.id" :value="item.id">
                      {{ item.name }} · {{ item.slug }}
                    </option>
                  </select>
                </label>
                <label>
                  应用方式
                  <select v-model="personaApplyMode">
                    <option value="append">追加到现有提示词</option>
                    <option value="replace">替换现有提示词</option>
                  </select>
                </label>
                <button class="btn primary small" @click="applyPersonaDraft" :disabled="personaReviewBusy || !selectedPersonaId">
                  应用学习结果
                </button>
              </div>
              <p v-if="analysisBundle.persona_draft.status === 'applied'" class="chat-analysis-hint">
                已应用到 Persona ID：{{ analysisBundle.persona_draft.applied_persona_id }}
              </p>
            </section>

            <section class="chat-review-section">
              <div class="chat-review-heading">
                <div>
                  <b>6.3 FAQ 高频问题候选</b>
                  <small>仅保留本人明确回答过的问题；冲突答案分开审核。</small>
                </div>
                <span class="chat-counter">{{ analysisBundle.faq_candidates.length }} 条</span>
              </div>
              <div v-if="analysisBundle.faq_candidates.length" class="chat-faq-publish-bar">
                <label>
                  发布到知识库
                  <select v-model="publishKnowledgeBaseId">
                    <option value="">请选择知识库</option>
                    <option v-for="item in knowledgeBases" :key="item.id" :value="item.id">
                      {{ item.name }}
                    </option>
                  </select>
                </label>
              </div>
              <div v-if="analysisBundle.faq_candidates.length" class="chat-faq-list">
                <article v-for="candidate in analysisBundle.faq_candidates" :key="candidate.id" class="chat-faq-card">
                  <div class="chat-faq-card-heading">
                    <b>{{ candidate.title }}</b>
                    <span class="chat-review-status">{{ reviewStatusLabel(candidate.status) }}</span>
                  </div>
                  <div class="chat-faq-meta">
                    <span>{{ candidate.category }}</span>
                    <span>出现 {{ candidate.occurrence_count }} 次</span>
                    <span>置信度 {{ Math.round(candidate.confidence * 100) }}%</span>
                    <span v-if="candidate.effective_date">适用时间：{{ candidate.effective_date }}</span>
                  </div>
                  <label class="chat-review-field">
                    标准问题
                    <textarea v-model="candidate.canonical_question" rows="2" maxlength="2000" :disabled="candidate.status === 'published' || candidate.status === 'rejected'"></textarea>
                  </label>
                  <div v-if="candidate.question_variants.length" class="chat-faq-variants">
                    <span v-for="variant in candidate.question_variants" :key="variant">{{ variant }}</span>
                  </div>
                  <label class="chat-review-field">
                    标准答案
                    <textarea v-model="candidate.answer" rows="4" maxlength="10000" :disabled="candidate.status === 'published' || candidate.status === 'rejected'"></textarea>
                  </label>
                  <div class="chat-faq-edit-grid">
                    <label>标题<input v-model="candidate.title" maxlength="160" :disabled="candidate.status === 'published' || candidate.status === 'rejected'"></label>
                    <label>分类<input v-model="candidate.category" maxlength="60" :disabled="candidate.status === 'published' || candidate.status === 'rejected'"></label>
                    <label>适用日期<input v-model="candidate.effective_date" maxlength="32" placeholder="例如：2026-11-15" :disabled="candidate.status === 'published' || candidate.status === 'rejected'"></label>
                  </div>
                  <div class="chat-review-actions">
                    <button
                      class="btn secondary small"
                      @click="saveFaqCandidate(candidate)"
                      :disabled="faqBusyId === candidate.id || ['published', 'rejected'].includes(candidate.status)"
                    >保存修改</button>
                    <button
                      class="btn primary small"
                      @click="reviewFaq(candidate.id, 'approve')"
                      :disabled="faqBusyId === candidate.id || candidate.status !== 'pending_review'"
                    >批准</button>
                    <button
                      class="btn secondary small danger-outline"
                      @click="reviewFaq(candidate.id, 'reject')"
                      :disabled="faqBusyId === candidate.id || !['pending_review', 'approved'].includes(candidate.status)"
                    >拒绝</button>
                    <button
                      class="btn primary small"
                      @click="publishFaq(candidate.id)"
                      :disabled="faqBusyId === candidate.id || candidate.status !== 'approved' || !publishKnowledgeBaseId"
                    >发布到知识库</button>
                  </div>
                  <p v-if="candidate.published_document_id" class="chat-analysis-hint">
                    已发布文档：{{ candidate.published_document_id }}
                  </p>
                </article>
              </div>
              <div v-else-if="analysisBundle.analysis_job.status === 'succeeded'" class="empty chat-compact-empty">
                <FileText :size="22" /><small>本次没有提取到足够可靠的 FAQ 候选。可以检查导入数据中是否包含对方问题及本人的明确答复。</small>
              </div>
            </section>
          </template>
        </section>

        <section v-if="!preview?.identity_confirmed" class="panel">
          <div class="panel-title">
            <div><b>确认本人身份</b><small>必须由用户明确选择，系统不会根据昵称或消息数量自动判断。</small></div>
            <label class="chat-status-pending">待确认</label>
          </div>
          <div v-if="preview?.senders.length" class="chat-participant-list">
            <button
              v-for="sender in preview.senders"
              :key="sender.sender_ref"
              class="chat-participant-row"
              :class="{ selected: selectedSenderRef === sender.sender_ref }"
              @click="selectedSenderRef = sender.sender_ref"
            >
              <span class="chat-participant-avatar"><UserRound :size="16" /></span>
              <span class="chat-participant-copy">
                <b>{{ sender.sender_name }}</b>
                <small>{{ sender.sender_ref }}</small>
              </span>
              <span class="chat-participant-count">{{ sender.message_count.toLocaleString() }} 条文本消息</span>
              <Check v-if="selectedSenderRef === sender.sender_ref" :size="16" />
            </button>
            <div class="chat-identity-form">
              <span v-if="selectedSender">已选择：{{ selectedSender.sender_name }}（{{ selectedSender.message_count }} 条）</span>
              <span v-else>请选择代表当前登录微信账号本人的发送者</span>
              <button class="btn primary small" @click="confirmIdentity" :disabled="identityBusy || !selectedSender">
                <Check :size="14" />{{ identityBusy ? '确认中…' : '确认这是本人' }}
              </button>
            </div>
          </div>
          <div v-else class="empty chat-compact-empty">
            <Users :size="25" /><b>等待文本解析</b>
            <small>解析完成后，仅显示发送者名称、标识和文本消息数量，不显示聊天正文。</small>
          </div>
        </section>

        <section v-else class="panel chat-identity-confirmed">
          <div class="chat-identity-state">
            <CheckCircle2 :size="18" />
            <span>已确认本人：{{ source.target_sender_name || source.target_sender_ref }}</span>
          </div>
          <small>该身份仅绑定当前 Source Account ID，不会与其他微信账号进行匹配或共享。</small>
        </section>
      </div>

      <section v-else class="panel empty chat-no-source">
        <Database :size="34" /><b>选择或创建一个来源账号</b>
        <small>每个微信登录账号使用独立的 Source Account ID。聊天文本仅用于提炼人格设定和知识库，不作为聊天记录保存。</small>
        <button class="btn primary" @click="sourceModal = true"><Plus :size="15" />创建来源账号</button>
      </section>
    </div>

    <div v-if="sourceModal" class="modal-backdrop" @click.self="sourceModal = false">
      <section class="modal small-modal">
        <div class="modal-title">
          <div><small>WECHAT SOURCE ACCOUNT</small><h2>创建独立来源账号</h2><p>每个登录微信账号单独创建，不按 wxid 自动合并。</p></div>
          <button class="icon-action" @click="sourceModal = false" aria-label="关闭"><X :size="18" /></button>
        </div>
        <form class="form-grid" @submit.prevent="createSource">
          <label class="full">账号备注名称 <b>*</b><input v-model="sourceForm.name" maxlength="120" placeholder="例如：个人微信主账号" required></label>
          <label class="full">导出工具
            <select v-model="sourceForm.export_tool">
              <option value="WeChat">微信通用导出</option>
              <option value="WeKeep">WeKeep</option>
              <option value="WeFlow">WeFlow</option>
              <option value="WeChatMsg">WeChatMsg</option>
              <option value="Other">其他 / 通用格式</option>
            </select>
          </label>
          <div class="modal-actions full">
            <button class="btn secondary" type="button" @click="sourceModal = false">取消</button>
            <button class="btn primary" type="submit" :disabled="sourceBusy">
              <Plus :size="14" />{{ sourceBusy ? '创建中…' : '创建来源账号' }}
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
