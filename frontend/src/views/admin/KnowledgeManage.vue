<script setup lang="ts">
/** 知识库管理：源文档上传 / 列表 / 预览 / 切片 / 重新处理 / 删除 */
import MarkdownIt from 'markdown-it'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { Upload } from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  deleteDocument,
  getDocument,
  getMd,
  listChunks,
  listDocuments,
  previewUrl,
  reprocessDocument,
  uploadDocument,
} from '../../api/admin'
import type { ChunkItem, DocumentItem } from '../../types'

const md = new MarkdownIt({ html: false, linkify: true })

const documents = ref<DocumentItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
// 多文件并发上传时的在途计数（>0 即显示 loading，全部完成后复位）
const uploadingCount = ref(0)
const uploading = computed(() => uploadingCount.value > 0)
let pollTimer: number | null = null

const hasActive = computed(() =>
  documents.value.some((d) => d.status === 'pending' || d.status === 'processing'),
)

async function refresh() {
  loading.value = true
  try {
    const data = await listDocuments((page.value - 1) * pageSize.value, pageSize.value)
    documents.value = data.documents
    total.value = data.total
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
    schedulePoll()
  }
}

function handlePageChange(p: number) {
  page.value = p
  refresh()
}

function handleSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
  refresh()
}

function schedulePoll() {
  if (pollTimer) return
  if (!hasActive.value) return
  pollTimer = window.setTimeout(async () => {
    pollTimer = null
    await refresh()
  }, 3000)
}

async function handleUpload(options: UploadRequestOptions): Promise<void> {
  // el-upload multiple 模式下每个文件各触发一次 http-request，逐个上传即可
  const file = options.file
  uploadingCount.value++
  try {
    await uploadDocument(file)
    ElMessage.success(`「${file.name}」已提交处理`)
    await refresh()
  } catch (e) {
    ElMessage.error(`「${file.name}」上传失败：${(e as Error).message}`)
  } finally {
    uploadingCount.value--
  }
}

async function handleReprocess(row: DocumentItem) {
  try {
    await reprocessDocument(row.id)
    ElMessage.success('已提交重新处理')
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function handleDelete(row: DocumentItem) {
  try {
    await deleteDocument(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

const statusText: Record<string, string> = {
  pending: '待处理',
  processing: '处理中',
  success: '成功',
  failure: '失败',
}
const statusType: Record<string, 'info' | 'warning' | 'success' | 'danger'> = {
  pending: 'info',
  processing: 'warning',
  success: 'success',
  failure: 'danger',
}

// ---------- 预览 ----------
const previewVisible = ref(false)
const previewDoc = ref<DocumentItem | null>(null)
const previewTab = ref('word')
const mdContent = ref('')
const chunks = ref<ChunkItem[]>([])
const loadingDetail = ref(false)

async function openPreview(row: DocumentItem) {
  previewDoc.value = row
  previewVisible.value = true
  previewTab.value = 'word'
  await loadDetail(row.id)
}

async function loadDetail(id: string) {
  loadingDetail.value = true
  try {
    const [mdData, chunkData] = await Promise.all([getMd(id), listChunks(id)])
    mdContent.value = mdData.content
    chunks.value = chunkData.chunks
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loadingDetail.value = false
  }
}

const parentChunks = computed(() => chunks.value.filter((c) => c.chunk_type === 'parent'))
const childChunks = computed(() => chunks.value.filter((c) => c.chunk_type === 'child'))

// ---------- Markdown 目录（TOC） ----------
interface TocItem {
  level: number
  text: string
  id: string
}

/** 渲染 markdown 并给标题注入锚点 id，同步产出目录（渲染与目录同源同序）。 */
const mdRendered = computed<{ html: string; toc: TocItem[] }>(() => {
  const toc: TocItem[] = []
  let idx = 0
  const html = md
    .render(mdContent.value)
    .replace(/<h([1-6])>([^<]*)<\/h\1>/g, (_m, lv: string, text: string) => {
      const id = `md-h-${idx++}`
      toc.push({ level: Number(lv), text, id })
      return `<h${lv} id="${id}">${text}</h${lv}>`
    })
  return { html, toc }
})

const mdToc = computed(() => mdRendered.value.toc)
const mdHtml = computed(() => mdRendered.value.html)

const mdScrollRef = ref<HTMLElement>()
const activeHeadingId = ref('')

function scrollToHeading(id: string) {
  activeHeadingId.value = id
  mdScrollRef.value
    ?.querySelector(`#${id}`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// ---------- 切片内容弹窗 ----------
const chunkDialogVisible = ref(false)
const activeChunk = ref<ChunkItem | null>(null)

function openChunk(chunk: ChunkItem) {
  activeChunk.value = chunk
  chunkDialogVisible.value = true
}

function renderChunkContent(chunk: ChunkItem) {
  // 切片 content 里的图片为 ./media/xxx.png 相对路径，渲染前改写为绝对链接
  const docName = previewDoc.value?.doc_name ?? ''
  const content = chunk.content.replace(
    /!\[([^\]]*)\]\(\.\/media\/([^)]+)\)/g,
    (_m, alt: string, filename: string) =>
      `![${alt}](/api/images/${encodeURIComponent(docName)}/${encodeURIComponent(filename)})`,
  )
  return md.render(content)
}

onMounted(refresh)
onUnmounted(() => {
  if (pollTimer) window.clearTimeout(pollTimer)
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-[16px] font-semibold text-ink">知识库管理</h2>
        <p class="text-[12px] text-ink-sub">上传 Word 源文档，自动转换 / 清洗 / 父子切片 / 向量化入库</p>
      </div>
      <el-upload
        accept=".docx,.doc"
        multiple
        :show-file-list="false"
        :http-request="handleUpload"
      >
        <el-button type="primary" :loading="uploading">
          <Upload :size="14" class="mr-1 inline" />
          上传文档
        </el-button>
      </el-upload>
    </div>

    <el-table :data="documents" v-loading="loading" class="rounded-xl" border>
      <el-table-column prop="doc_name" label="手册名" min-width="220" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType[row.status]" size="small">{{ statusText[row.status] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="切片数" width="90">
        <template #default="{ row }">{{ row.chunk_count }}</template>
      </el-table-column>
      <el-table-column label="大小" width="100">
        <template #default="{ row }">{{ (row.file_size / 1024).toFixed(0) }} KB</template>
      </el-table-column>
      <el-table-column prop="created_at" label="上传时间" width="180" />
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openPreview(row)">预览</el-button>
          <el-button link type="primary" @click="handleReprocess(row)">重新处理</el-button>
          <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > 0"
      class="justify-end"
      layout="total, sizes, prev, pager, next"
      :total="total"
      :current-page="page"
      :page-size="pageSize"
      :page-sizes="[10, 20, 50]"
      @current-change="handlePageChange"
      @size-change="handleSizeChange"
    />

    <!-- 预览对话框（全屏） -->
    <el-dialog
      v-model="previewVisible"
      :title="previewDoc?.doc_name"
      fullscreen
      destroy-on-close
      class="preview-dialog"
    >
      <el-tabs v-model="previewTab" class="preview-tabs">
        <el-tab-pane label="Word 预览" name="word">
          <div v-if="previewDoc" class="h-full overflow-hidden rounded-lg border border-muted">
            <iframe :src="previewUrl(previewDoc.id)" class="h-full w-full" />
          </div>
        </el-tab-pane>
        <el-tab-pane label="Markdown" name="md">
          <div v-loading="loadingDetail" class="flex h-full gap-3">
            <!-- 目录侧边栏 -->
            <aside
              v-if="mdToc.length"
              class="w-52 shrink-0 overflow-y-auto rounded-lg border border-muted bg-white/60 p-2"
            >
              <div class="mb-1 px-1 text-[12px] font-medium text-ink-sub">目录</div>
              <button
                v-for="t in mdToc"
                :key="t.id"
                class="block w-full cursor-pointer truncate rounded px-1.5 py-1 text-left text-[12px] transition-colors"
                :class="
                  activeHeadingId === t.id
                    ? 'bg-primary/10 font-medium text-primary'
                    : 'text-ink-sub hover:bg-muted hover:text-primary'
                "
                :style="{ paddingLeft: `${(t.level - 1) * 10 + 6}px` }"
                :title="t.text"
                @click="scrollToHeading(t.id)"
              >
                {{ t.text }}
              </button>
            </aside>

            <!-- 正文（标题带锚点 id，供目录跳转） -->
            <div
              ref="mdScrollRef"
              class="md-body h-full min-w-0 flex-1 overflow-y-auto rounded-lg border border-muted p-4"
              v-html="mdHtml"
            />
          </div>
        </el-tab-pane>
        <el-tab-pane label="切片列表" name="chunks">
          <div v-loading="loadingDetail" class="space-y-4">
            <div class="text-[12px] text-ink-sub">
              父切片 {{ parentChunks.length }} 个 · 子切片 {{ childChunks.length }} 个
            </div>
            <el-table :data="chunks" size="small" border max-height="400">
              <el-table-column label="类型" width="70">
                <template #default="{ row }">
                  <el-tag :type="row.chunk_type === 'parent' ? 'primary' : 'success'" size="small">
                    {{ row.chunk_type === 'parent' ? '父' : '子' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="path" label="路径" min-width="200" />
              <el-table-column label="字符" width="70">
                <template #default="{ row }">{{ row.char_count }}</template>
              </el-table-column>
              <el-table-column label="图" width="50">
                <template #default="{ row }">{{ row.image_count }}</template>
              </el-table-column>
              <el-table-column label="向量状态" width="90">
                <template #default="{ row }">
                  <el-tag v-if="row.chunk_type === 'child'" :type="row.vector_state === 'success' ? 'success' : row.vector_state === 'failure' ? 'danger' : 'info'" size="small">
                    {{ row.vector_state ?? '-' }}
                  </el-tag>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column label="内容" width="80" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="openChunk(row)">查看</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-dialog>

    <!-- 切片内容弹窗 -->
    <el-dialog
      v-model="chunkDialogVisible"
      :title="activeChunk ? `${activeChunk.chunk_type === 'parent' ? '父切片' : '子切片'}：${activeChunk.path}` : '切片内容'"
      width="760px"
      top="6vh"
    >
      <div
        v-if="activeChunk"
        class="md-body max-h-[70vh] overflow-y-auto rounded-lg border border-muted p-4"
        v-html="renderChunkContent(activeChunk)"
      />
    </el-dialog>
  </div>
</template>

<style scoped>
/* 全屏预览弹窗：内容区撑满剩余高度（减去 dialog 头与 tabs 头） */
:global(.preview-dialog .el-dialog__body) {
  height: calc(100vh - 110px);
  overflow: hidden;
}
:global(.preview-tabs) {
  height: 100%;
  display: flex;
  flex-direction: column;
}
:global(.preview-tabs .el-tabs__content) {
  flex: 1;
  overflow: hidden;
}
:global(.preview-tabs .el-tab-pane) {
  height: 100%;
}
</style>
