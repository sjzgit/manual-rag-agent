<script setup lang="ts">
/** 用户反馈：反馈列表 + 工单归集（反馈侧发起关联，工单四态自由切换） */
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import {
  createTicket,
  getAdminTicket,
  linkFeedbacksToTicket,
  listAdminFeedbacks,
  listAdminTickets,
  unlinkFeedbackFromTicket,
  updateTicket,
} from '../../api/admin'
import type {
  FeedbackItem,
  TicketDetail,
  TicketItem,
  TicketPriority,
  TicketStatus,
} from '../../types'
import SessionDetailDrawer from './SessionDetailDrawer.vue'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

// ---------- 反馈列表 ----------
const feedbacks = ref<FeedbackItem[]>([])
const feedbackTotal = ref(0)
const feedbackLoading = ref(false)
const selectedFeedbacks = ref<FeedbackItem[]>([])
const feedbackTableRef = ref()

function scoreTag(score: number): { text: string; type: TagType } {
  return score === 1 ? { text: '赞', type: 'success' } : { text: '踩', type: 'danger' }
}

async function refreshFeedbacks() {
  feedbackLoading.value = true
  try {
    const data = await listAdminFeedbacks(0, 100)
    feedbacks.value = data.feedbacks
    feedbackTotal.value = data.total
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    feedbackLoading.value = false
  }
}

function onSelectionChange(rows: FeedbackItem[]) {
  selectedFeedbacks.value = rows
}

function clearSelection() {
  selectedFeedbacks.value = []
  feedbackTableRef.value?.clearSelection()
}

// ---------- 工单列表 ----------
const tickets = ref<TicketItem[]>([])
const ticketTotal = ref(0)
const ticketLoading = ref(false)

const statusTag: Record<TicketStatus, { text: string; type: TagType }> = {
  pending: { text: '待处理', type: 'warning' },
  processing: { text: '处理中', type: 'primary' },
  resolved: { text: '已处理', type: 'success' },
  closed: { text: '已关闭', type: 'info' },
}

const priorityTag: Record<TicketPriority, { text: string; type: TagType }> = {
  high: { text: '高', type: 'danger' },
  medium: { text: '中', type: 'warning' },
  low: { text: '低', type: 'info' },
}

async function refreshTickets() {
  ticketLoading.value = true
  try {
    const data = await listAdminTickets(0, 100)
    tickets.value = data.tickets
    ticketTotal.value = data.total
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    ticketLoading.value = false
  }
}

// ---------- 会话详情抽屉 ----------
const drawerVisible = ref(false)
const selectedSessionId = ref('')

function openSession(sessionId: string | null) {
  if (!sessionId) return
  selectedSessionId.value = sessionId
  drawerVisible.value = true
}

// ---------- 新建工单（并关联） ----------
const createDialogVisible = ref(false)
const createPreview = ref<FeedbackItem[]>([])
const createForm = ref({ title: '', priority: 'medium' as TicketPriority })

function openCreateTicket() {
  createPreview.value = [...selectedFeedbacks.value]
  createForm.value = {
    title: (selectedFeedbacks.value[0]?.comment ?? '').slice(0, 50),
    priority: 'medium',
  }
  createDialogVisible.value = true
}

function openEmptyCreateTicket() {
  createPreview.value = []
  createForm.value = { title: '', priority: 'medium' }
  createDialogVisible.value = true
}

async function submitCreateTicket() {
  if (!createForm.value.title.trim()) {
    ElMessage.warning('请输入工单标题')
    return
  }
  try {
    await createTicket({
      title: createForm.value.title.trim(),
      priority: createForm.value.priority,
      feedback_ids: createPreview.value.map((f) => f.id),
    })
    ElMessage.success('工单已创建')
    createDialogVisible.value = false
    clearSelection()
    await refreshTickets()
    activeTab.value = 'tickets'
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

// ---------- 加入已有工单 ----------
const linkDialogVisible = ref(false)
const linkTicketId = ref<number | null>(null)
const linkPreview = ref<FeedbackItem[]>([])

function openLinkTicket() {
  linkPreview.value = [...selectedFeedbacks.value]
  linkTicketId.value = null
  linkDialogVisible.value = true
}

async function submitLinkTicket() {
  if (!linkTicketId.value) {
    ElMessage.warning('请选择工单')
    return
  }
  try {
    const res = await linkFeedbacksToTicket(
      linkTicketId.value,
      linkPreview.value.map((f) => f.id),
    )
    ElMessage.success(`已关联，新增 ${res.linked} 条`)
    linkDialogVisible.value = false
    clearSelection()
    await refreshTickets()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

// ---------- 工单详情 ----------
const detailDialogVisible = ref(false)
const detail = ref<TicketDetail | null>(null)
const editForm = ref({
  title: '',
  status: 'pending' as TicketStatus,
  priority: 'medium' as TicketPriority,
  result: '',
})

async function openTicketDetail(row: TicketItem) {
  detailDialogVisible.value = true
  detail.value = null
  try {
    const d = await getAdminTicket(row.id)
    detail.value = d
    editForm.value = {
      title: d.ticket.title,
      status: d.ticket.status,
      priority: d.ticket.priority,
      result: d.ticket.result ?? '',
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function reloadDetail() {
  if (!detail.value) return
  try {
    detail.value = await getAdminTicket(detail.value.ticket.id)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function saveTicket() {
  if (!detail.value) return
  try {
    await updateTicket(detail.value.ticket.id, {
      title: editForm.value.title,
      status: editForm.value.status,
      priority: editForm.value.priority,
      result: editForm.value.result,
    })
    ElMessage.success('已保存')
    detailDialogVisible.value = false
    await refreshTickets()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function unlink(feedbackId: number) {
  if (!detail.value) return
  try {
    await unlinkFeedbackFromTicket(detail.value.ticket.id, feedbackId)
    ElMessage.success('已解除关联')
    await reloadDetail()
    await refreshTickets()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

const activeTab = ref('feedbacks')

onMounted(() => {
  refreshFeedbacks()
  refreshTickets()
})
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="text-[16px] font-semibold text-ink">用户反馈</h2>
      <p class="text-[12px] text-ink-sub">
        查看用户点赞/踩反馈，并将反馈归集为工单跟踪处理
      </p>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="反馈列表" name="feedbacks">
        <div class="space-y-3">
          <div class="flex items-center gap-2">
            <el-button
              type="primary"
              :disabled="!selectedFeedbacks.length"
              @click="openCreateTicket"
            >
              新建工单并关联（{{ selectedFeedbacks.length }}）
            </el-button>
            <el-button :disabled="!selectedFeedbacks.length" @click="openLinkTicket">
              加入已有工单
            </el-button>
          </div>

          <el-table
            ref="feedbackTableRef"
            :data="feedbacks"
            v-loading="feedbackLoading"
            border
            class="rounded-xl"
            @selection-change="onSelectionChange"
          >
            <el-table-column type="selection" width="50" />
            <el-table-column label="分值" width="80">
              <template #default="{ row }">
                <el-tag :type="scoreTag(row.score).type" size="small">
                  {{ scoreTag(row.score).text }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="comment" label="反馈内容" min-width="200" show-overflow-tooltip />
            <el-table-column label="会话标题" min-width="180">
              <template #default="{ row }">{{ row.session_title ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="消息内容" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">{{ row.message_content ?? '-' }}</template>
            </el-table-column>
            <el-table-column prop="created_at" label="反馈时间" width="180" />
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  :disabled="!row.session_id"
                  @click="openSession(row.session_id)"
                >
                  查看会话
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="工单列表" name="tickets">
        <div class="space-y-3">
          <div>
            <el-button type="primary" @click="openEmptyCreateTicket">新建工单</el-button>
          </div>

          <el-table :data="tickets" v-loading="ticketLoading" border class="rounded-xl">
            <el-table-column prop="title" label="标题" min-width="220" />
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="statusTag[row.status as TicketStatus].type" size="small">
                  {{ statusTag[row.status as TicketStatus].text }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="优先级" width="90">
              <template #default="{ row }">
                <el-tag :type="priorityTag[row.priority as TicketPriority].type" size="small">
                  {{ priorityTag[row.priority as TicketPriority].text }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="feedback_count" label="关联反馈" width="100" />
            <el-table-column label="处理时间" width="180">
              <template #default="{ row }">{{ row.processed_at ?? '-' }}</template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="180" />
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openTicketDetail(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 新建工单并关联 弹窗 -->
    <el-dialog v-model="createDialogVisible" title="新建工单并关联" width="560px">
      <div class="space-y-4">
        <div>
          <div class="mb-1 text-[12px] text-ink-sub">工单标题</div>
          <el-input v-model="createForm.title" placeholder="请输入工单标题" />
        </div>
        <div>
          <div class="mb-1 text-[12px] text-ink-sub">优先级</div>
          <el-select v-model="createForm.priority" class="w-full">
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
        </div>
        <div>
          <div class="mb-1 text-[12px] text-ink-sub">已选反馈（{{ createPreview.length }}）</div>
          <div class="max-h-40 space-y-1 overflow-y-auto">
            <div
              v-for="f in createPreview"
              :key="f.id"
              class="rounded bg-muted px-2.5 py-1.5 text-[12px]"
            >
              <el-tag :type="scoreTag(f.score).type" size="small" class="mr-1">
                {{ scoreTag(f.score).text }}
              </el-tag>
              {{ f.comment || f.session_title || '-' }}
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreateTicket">确定</el-button>
      </template>
    </el-dialog>

    <!-- 加入已有工单 弹窗 -->
    <el-dialog v-model="linkDialogVisible" title="加入已有工单" width="560px">
      <div class="space-y-4">
        <div>
          <div class="mb-1 text-[12px] text-ink-sub">选择工单</div>
          <el-select v-model="linkTicketId" class="w-full" placeholder="请选择工单">
            <el-option
              v-for="t in tickets"
              :key="t.id"
              :label="`#${t.id} ${t.title}（${statusTag[t.status].text}）`"
              :value="t.id"
            />
          </el-select>
        </div>
        <div>
          <div class="mb-1 text-[12px] text-ink-sub">已选反馈（{{ linkPreview.length }}）</div>
          <div class="max-h-40 space-y-1 overflow-y-auto">
            <div
              v-for="f in linkPreview"
              :key="f.id"
              class="rounded bg-muted px-2.5 py-1.5 text-[12px]"
            >
              {{ f.comment || f.session_title || '-' }}
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="linkDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitLinkTicket">确定</el-button>
      </template>
    </el-dialog>

    <!-- 工单详情 弹窗 -->
    <el-dialog v-model="detailDialogVisible" title="工单详情" width="720px" top="6vh">
      <div v-if="detail" class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div class="col-span-2">
            <div class="mb-1 text-[12px] text-ink-sub">标题</div>
            <el-input v-model="editForm.title" />
          </div>
          <div>
            <div class="mb-1 text-[12px] text-ink-sub">状态</div>
            <el-select v-model="editForm.status" class="w-full">
              <el-option label="待处理" value="pending" />
              <el-option label="处理中" value="processing" />
              <el-option label="已处理" value="resolved" />
              <el-option label="已关闭" value="closed" />
            </el-select>
          </div>
          <div>
            <div class="mb-1 text-[12px] text-ink-sub">优先级</div>
            <el-select v-model="editForm.priority" class="w-full">
              <el-option label="高" value="high" />
              <el-option label="中" value="medium" />
              <el-option label="低" value="low" />
            </el-select>
          </div>
          <div class="col-span-2">
            <div class="mb-1 text-[12px] text-ink-sub">处理结果</div>
            <el-input v-model="editForm.result" type="textarea" :rows="3" placeholder="处理结果" />
          </div>
        </div>
        <div>
          <div class="mb-1 text-[12px] text-ink-sub">关联反馈（{{ detail.feedbacks.length }}）</div>
          <el-table :data="detail.feedbacks" size="small" border>
            <el-table-column label="分值" width="80">
              <template #default="{ row }">
                <el-tag :type="scoreTag(row.score).type" size="small">
                  {{ scoreTag(row.score).text }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="comment" label="反馈内容" min-width="200" show-overflow-tooltip />
            <el-table-column label="会话标题" min-width="140">
              <template #default="{ row }">{{ row.session_title ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  size="small"
                  :disabled="!row.session_id"
                  @click="openSession(row.session_id)"
                >
                  查看会话
                </el-button>
                <el-button link type="danger" size="small" @click="unlink(row.id)">
                  解除关联
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
      <template #footer>
        <el-button @click="detailDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveTicket">保存</el-button>
      </template>
    </el-dialog>

    <SessionDetailDrawer v-model="drawerVisible" :session-id="selectedSessionId" />
  </div>
</template>
