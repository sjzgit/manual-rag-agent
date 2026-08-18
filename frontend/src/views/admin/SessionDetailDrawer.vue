<script setup lang="ts">
/** 会话详情抽屉：会话管理 / 用户反馈 共用的详情展示（对话 / 检索日志 / 意图日志 + LLM 输入输出） */
import MarkdownIt from 'markdown-it'
import { ElMessage } from 'element-plus'
import { ChevronRight } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { getAdminSession, getSessionLogs } from '../../api/admin'
import type { AdminSessionDetail, LlmCallLog, SessionLogs, StepEvent } from '../../types'

const props = defineProps<{ sessionId: string }>()
const modelValue = defineModel<boolean>({ required: true })

const md = new MarkdownIt({ html: false, linkify: true })

const detail = ref<AdminSessionDetail | null>(null)
const logs = ref<SessionLogs | null>(null)
const detailTab = ref('chat')
const expandedRounds = ref<Set<number>>(new Set())

async function load() {
  detail.value = null
  logs.value = null
  detailTab.value = 'chat'
  expandedRounds.value = new Set()
  try {
    const [d, l] = await Promise.all([getAdminSession(props.sessionId), getSessionLogs(props.sessionId)])
    detail.value = d
    logs.value = l
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

watch(modelValue, (open) => {
  if (open) load()
})

function render(content: string) {
  return md.render(content)
}

// ---------- 轮次分组（以用户输入为导航） ----------
type Msg = AdminSessionDetail['messages'][number]
interface Round {
  user: Msg
  assistants: Msg[]
}

const rounds = computed<Round[]>(() => {
  const result: Round[] = []
  for (const m of detail.value?.messages ?? []) {
    if (m.role === 'user') result.push({ user: m, assistants: [] })
    else if (result.length) result[result.length - 1].assistants.push(m)
  }
  return result
})

function toggleRound(i: number) {
  const s = new Set(expandedRounds.value)
  if (s.has(i)) s.delete(i)
  else s.add(i)
  expandedRounds.value = s
}

// ---------- 过程步骤 ----------
const stepTypeLabel: Record<string, string> = {
  intent: '意图识别',
  retrieve: '检索',
  thinking: '生成',
  tool_call: '工具调用',
}

function intentBadge(t: string): { text: string; type: 'success' | 'warning' | 'danger' | 'info' } {
  const map: Record<string, { text: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
    precise: { text: '精确', type: 'success' },
    vague: { text: '模糊', type: 'warning' },
    irrelevant: { text: '不相关', type: 'danger' },
  }
  return map[t] ?? { text: t, type: 'info' }
}

function stepHits(step: StepEvent) {
  return (step.detail?.hits ?? []) as Array<{ doc: string; path: string; score: number }>
}

// ---------- LLM 输入输出 ----------
const llmDialogVisible = ref(false)
const activeLlmLogs = ref<LlmCallLog[]>([])

function llmCallsFor(messageId: string): LlmCallLog[] {
  return (logs.value?.llm_call_logs ?? []).filter((l) => l.message_id === messageId)
}

function openLlmLogs(messageId: string, callType: 'intent' | 'answer') {
  activeLlmLogs.value = llmCallsFor(messageId).filter((l) => l.call_type === callType)
  if (activeLlmLogs.value.length) llmDialogVisible.value = true
}

function renderOutput(log: LlmCallLog) {
  return log.call_type === 'answer' ? md.render(log.output) : log.output
}
</script>

<template>
  <el-drawer
    v-model="modelValue"
    :title="detail?.session.title ?? '会话详情'"
    size="60%"
    destroy-on-close
  >
    <el-tabs v-model="detailTab">
      <el-tab-pane label="对话" name="chat">
        <div v-if="detail" class="space-y-3">
          <div v-for="(round, ri) in rounds" :key="ri" class="overflow-hidden rounded-xl border border-muted">
            <!-- 用户输入（导航） -->
            <button
              class="flex w-full cursor-pointer items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-muted/50"
              @click="toggleRound(ri)"
            >
              <ChevronRight
                :size="14"
                class="shrink-0 text-ink-sub transition-transform"
                :class="{ 'rotate-90': expandedRounds.has(ri) }"
              />
              <span class="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">
                {{ round.user.content }}
              </span>
              <span class="shrink-0 text-[11px] text-ink-sub">用户</span>
            </button>

            <!-- 展开：助手回答 + 来源切片 + 过程步骤 -->
            <div v-if="expandedRounds.has(ri)" class="space-y-3 border-t border-muted px-3.5 py-3">
              <div v-for="assistant in round.assistants" :key="assistant.id">
                <!-- 过程步骤 -->
                <div v-if="assistant.steps.length" class="mb-2">
                  <el-collapse>
                    <el-collapse-item :title="`过程步骤（${assistant.steps.length}）`">
                      <div class="space-y-2">
                        <div v-for="(st, i) in assistant.steps" :key="i" class="rounded-lg border border-muted p-2.5">
                          <div class="flex items-center gap-2 text-[12px] font-medium text-ink">
                            <span class="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[11px] text-primary">
                              {{ stepTypeLabel[st.type] ?? st.type }}
                            </span>
                            <span class="min-w-0 flex-1 truncate">{{ st.title }}</span>
                            <el-button
                              v-if="st.type === 'intent' || st.type === 'thinking'"
                              link
                              type="primary"
                              size="small"
                              class="shrink-0"
                              @click="openLlmLogs(assistant.id, st.type === 'intent' ? 'intent' : 'answer')"
                            >
                              查看输入输出
                            </el-button>
                          </div>

                          <!-- 意图识别明细 -->
                          <div v-if="st.detail?.intents" class="mt-2 space-y-1.5">
                            <div
                              v-for="(it, j) in st.detail.intents"
                              :key="j"
                              class="rounded-lg bg-muted/70 px-2.5 py-2 text-[12px]"
                            >
                              <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-ink">{{ it.simple_input || it.input }}</span>
                                <el-tag :type="intentBadge(it.intent_type).type" size="small">
                                  {{ intentBadge(it.intent_type).text }}
                                </el-tag>
                              </div>
                              <div class="mt-1 flex gap-1.5 flex-wrap text-ink-sub">
                                <span v-if="it.module" class="rounded bg-white px-1.5 py-0.5">模块：{{ it.module }}</span>
                                <span v-if="it.role" class="rounded bg-white px-1.5 py-0.5">角色：{{ it.role }}</span>
                                <span v-if="it.description" class="rounded bg-white px-1.5 py-0.5">{{ it.description }}</span>
                              </div>
                              <div v-if="it.intent_reason" class="mt-1 text-ink-sub/80">{{ it.intent_reason }}</div>
                            </div>
                          </div>

                          <!-- 检索命中明细 -->
                          <div v-else-if="stepHits(st).length" class="mt-2 space-y-1">
                            <div
                              v-for="(h, j) in stepHits(st)"
                              :key="j"
                              class="flex items-center gap-2 rounded-lg bg-muted/70 px-2.5 py-1.5 text-[12px]"
                            >
                              <span class="min-w-0 flex-1 truncate text-ink">{{ h.doc }} &gt; {{ h.path }}</span>
                              <span class="flex shrink-0 items-center gap-1.5">
                                <span class="h-1.5 w-16 overflow-hidden rounded-full bg-white">
                                  <span
                                    class="block h-full rounded-full bg-primary"
                                    :style="{ width: `${Math.min(100, h.score * 100)}%` }"
                                  />
                                </span>
                                <span class="text-ink-sub">{{ h.score.toFixed(3) }}</span>
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </div>

                <!-- 助手回答 -->
                <div class="rounded-lg bg-muted px-3 py-2.5">
                  <div class="mb-1 text-[11px] font-medium text-ink-sub">助手</div>
                  <div class="md-body text-[13px]" v-html="render(assistant.content)" />
                </div>

                <!-- 来源切片（每个默认收起） -->
                <div v-if="assistant.sources.length" class="mt-2">
                  <el-collapse>
                    <el-collapse-item
                      v-for="s in assistant.sources"
                      :key="s.chunk_id"
                      :title="`${s.doc} > ${s.path}（${(s.score * 100).toFixed(0)}%）`"
                    >
                      <div class="md-body text-[12px]" v-html="render(s.content)" />
                    </el-collapse-item>
                  </el-collapse>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="`检索日志（${logs?.retrieval_logs.length ?? 0}）`" name="retrieval">
        <el-table :data="logs?.retrieval_logs ?? []" size="small" border>
          <el-table-column prop="sub_question" label="检索语句" min-width="160" />
          <el-table-column prop="doc" label="手册" min-width="160" />
          <el-table-column prop="path" label="路径" min-width="160" />
          <el-table-column label="得分" width="80">
            <template #default="{ row }">{{ row.score.toFixed(3) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`意图日志（${logs?.intent_logs.length ?? 0}）`" name="intent">
        <el-table :data="logs?.intent_logs ?? []" size="small" border>
          <el-table-column label="意图" width="100">
            <template #default="{ row }">
              <el-tag size="small">{{ row.intent_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="识别内容" min-width="200">
            <template #default="{ row }">
              <span class="text-[12px]">{{ row.sub_question?.simple_input ?? row.sub_question?.input ?? '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="intent_reason" label="判断依据" min-width="240" />
          <el-table-column label="用 LLM" width="80">
            <template #default="{ row }">{{ row.used_llm ? '是' : '否' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </el-drawer>

  <!-- LLM 输入输出弹窗 -->
  <el-dialog v-model="llmDialogVisible" title="LLM 调用详情" width="80%" top="5vh">
    <div v-for="(log, i) in activeLlmLogs" :key="log.id" class="mb-4">
      <div v-if="activeLlmLogs.length > 1" class="mb-2 text-[12px] font-medium text-ink-sub">
        第 {{ i + 1 }} 次调用（{{ log.call_type === 'intent' ? '意图识别' : '回答生成' }}）
      </div>
      <el-tabs>
        <el-tab-pane label="输入">
          <div class="mb-2 text-[12px] font-medium text-ink">System Prompt</div>
          <pre class="mb-3 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-[12px] text-ink-sub">{{ log.system_prompt }}</pre>
          <div class="mb-2 text-[12px] font-medium text-ink">Messages</div>
          <div class="space-y-1.5">
            <div
              v-for="(msg, j) in log.messages"
              :key="j"
              class="rounded-lg bg-muted/70 px-2.5 py-2 text-[12px]"
            >
              <span class="mr-1 font-medium text-primary">{{ msg.role }}:</span>
              <span class="whitespace-pre-wrap text-ink">{{ msg.content }}</span>
            </div>
          </div>
        </el-tab-pane>
        <el-tab-pane label="输出">
          <pre
            v-if="log.call_type === 'intent'"
            class="max-h-[50vh] overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-[12px] text-ink"
          >{{ log.output }}</pre>
          <div v-else class="md-body max-h-[50vh] overflow-y-auto text-[13px]" v-html="renderOutput(log)" />
        </el-tab-pane>
      </el-tabs>
    </div>
  </el-dialog>
</template>
