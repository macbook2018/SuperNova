<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ChevronLeft, ChevronRight, X } from 'lucide-vue-next'
import { apiRequest, type ChunkLocation, type DocumentChunkPage, type KnowledgeDocument } from '../lib/api'

const props = defineProps<{ document: KnowledgeDocument }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const list = ref<HTMLElement | null>(null)
const result = ref<DocumentChunkPage | null>(null)
const error = ref('')
const loading = ref(false)
const offset = ref(0)
const pageSize = 10
let requestNumber = 0

async function loadPage(start: number, snapshot?: string) {
  const request = ++requestNumber
  const query = new URLSearchParams({ limit: String(pageSize), offset: String(start) })
  if (snapshot) query.set('snapshot', snapshot)
  loading.value = true
  error.value = ''
  try {
    const doc = props.document
    const page = await apiRequest<DocumentChunkPage>(
      '/knowledge-bases/' + doc.knowledge_base_id + '/documents/' + doc.id + '/chunks?' + query,
    )
    if (request !== requestNumber) return
    result.value = page
    offset.value = start
    list.value?.scrollTo(0, 0)
  } catch (cause) {
    if (request !== requestNumber) return
    result.value = null
    error.value = cause instanceof Error ? cause.message : '读取切片失败'
  } finally {
    if (request === requestNumber) loading.value = false
  }
}

function range(start: number, end = start) {
  return start === end ? String(start) : start + '–' + end
}

function locationText(loc: ChunkLocation) {
  const parts: string[] = []
  if (loc.page_number != null) parts.push('第 ' + loc.page_number + ' 页')
  if (loc.slide_number != null) parts.push('幻灯片 ' + loc.slide_number)
  if (loc.sheet_name) parts.push('工作表 ' + loc.sheet_name)
  if (loc.table_index != null) parts.push('表格 ' + (loc.table_index + 1))
  if (loc.cell_range) parts.push(loc.cell_range)
  if (loc.line_start != null) parts.push('第 ' + range(loc.line_start, loc.line_end) + ' 行')
  else if (loc.row_number != null) parts.push('第 ' + loc.row_number + ' 行')
  else if (loc.row_start != null) parts.push('第 ' + range(loc.row_start, loc.row_end) + ' 行')
  if (loc.paragraph_index != null) parts.push('段落 ' + (loc.paragraph_index + 1))
  if (loc.element_index != null) parts.push('元素 ' + (loc.element_index + 1))
  if (loc.page_char_start != null && loc.page_char_end != null) {
    parts.push('页内字符 ' + range(loc.page_char_start + 1, loc.page_char_end))
  } else {
    if (loc.block_index != null && (!parts.length || loc.start != null)) {
      parts.push('原文块 ' + (loc.block_index + 1))
    }
    if (loc.start != null && loc.end != null) parts.push('块内字符 ' + range(loc.start + 1, loc.end))
  }
  return parts.join(' · ')
}

onMounted(() => {
  dialog.value?.showModal()
  void loadPage(0)
})
onBeforeUnmount(() => { requestNumber++ })
</script>

<template>
  <dialog ref="dialog" class="chunk-preview" aria-labelledby="chunk-preview-title" @close="emit('close')">
    <header>
      <div>
        <h2 id="chunk-preview-title">切片预览</h2>
        <p>{{ document.original_filename }}</p>
      </div>
      <button class="btn secondary small" autofocus aria-label="关闭切片预览" @click="dialog?.close()"><X :size="16" /></button>
    </header>
    <div ref="list" class="chunks" :aria-busy="loading">
      <div v-if="error" class="status" role="alert">
        <p>{{ error }}</p><button class="btn secondary small" @click="loadPage(0)">重新读取首页</button>
      </div>
      <p v-else-if="!result" class="status" role="status">正在读取已发布切片…</p>
      <template v-else>
        <p class="version">{{ result.release_revision === null ? '文档索引 v' + result.document_index_version : '知识库发布版本 ' + result.release_revision }}</p>
        <p v-if="!result.items.length" class="status">此版本没有切片。</p>
        <article v-for="chunk in result.items" :key="chunk.id">
          <h3>切片 {{ chunk.chunk_index + 1 }}</h3>
          <p v-if="chunk.heading_path.length" class="heading-path">{{ chunk.heading_path.join(' › ') }}</p>
          <p class="body">{{ chunk.content }}</p>
          <details v-if="chunk.locations.length > 1">
            <summary>原文位置 · {{ locationText(chunk.locations[0]) }}</summary>
            <ul>
              <li v-for="(loc, i) in chunk.locations.slice(1)" :key="i">{{ locationText(loc) }}</li>
            </ul>
          </details>
          <small v-else-if="chunk.locations.length">原文位置 · {{ locationText(chunk.locations[0]) }}</small>
          <small v-else>此索引未记录原文位置</small>
        </article>
      </template>
    </div>
    <footer v-if="result && result.items.length">
      <small aria-live="polite">第 {{ Math.floor(offset / pageSize) + 1 }} 页</small>
      <div>
        <button class="btn secondary small" :disabled="loading || offset === 0" @click="loadPage(offset - pageSize, result.snapshot)"><ChevronLeft :size="15" />上一页</button>
        <button class="btn secondary small" :disabled="loading || !result.has_more" @click="loadPage(offset + pageSize, result.snapshot)">下一页<ChevronRight :size="15" /></button>
      </div>
    </footer>
  </dialog>
</template>

<style scoped>
.chunk-preview { width: min(720px, calc(100% - 32px)); max-height: 85vh; margin: auto; padding: 0; border: 1px solid #dce4ef; border-radius: 16px; color: #273750; background: white; box-shadow: 0 20px 65px #192b4d28; }
.chunk-preview[open] { display: flex; flex-direction: column; }
.chunk-preview::backdrop { background: #15233f66; }
header, footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 20px; }
header { border-bottom: 1px solid #e5ebf3; }
header div { min-width: 0; }
h2, h3, p, ul { margin: 0; }
h2 { font-size: 18px; } h3 { font-size: 14px; }
header p, .version, .heading-path, details, small { color: #596b83; font-size: 13px; }
header p { margin-top: 3px; overflow-wrap: anywhere; }
.chunks { overflow-y: auto; overscroll-behavior: contain; padding: 16px 20px; }
article { padding: 16px 0; border-bottom: 1px solid #e5ebf3; }
article:last-child { border-bottom: 0; }
.body { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0; line-height: 1.65; }
.heading-path, .status p { margin: 6px 0; }
details, .heading-path { overflow-wrap: anywhere; }
summary { cursor: pointer; } ul { padding-left: 22px; }
footer { border-top: 1px solid #e5ebf3; } footer div { display: flex; gap: 8px; }
</style>
