<script setup lang="ts">
/** 消息气泡：用户右 / AI 左，Markdown 渲染 + 步骤面板 + 来源卡片 + 澄清卡片 + 反馈条 */
import MarkdownIt from 'markdown-it'
import { ElMessage } from 'element-plus'
import { Brain, Check, ChevronDown, Copy, ThumbsDown, ThumbsUp, Trash2, User } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { ChatMessage } from '../types'
import ClarifyCard from './ClarifyCard.vue'
import SourceCard from './SourceCard.vue'
import StepPanel from './StepPanel.vue'

const props = defineProps<{
  message: ChatMessage
  generating?: boolean
}>()
const emit = defineEmits<{
  clarifySubmit: [answer: string]
  feedback: [msg: ChatMessage, score: 1 | -1, comment: string]
  delete: [msg: ChatMessage]
}>()

const md = new MarkdownIt({ html: false, linkify: true })
const rendered = computed(() => md.render(props.message.content || ''))

const showCommentFor = ref<-1 | 0>(0)
const comment = ref('')
const copied = ref(false)
const showReasoning = ref(false)

/** 复制文本到剪切板：Clipboard API 在非安全上下文（内网 HTTP IP）不可用，降级 execCommand。 */
async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      /* 降级到 execCommand */
    }
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

async function copy() {
  const text = props.message.content || ''
  if (!text) return
  const ok = await copyText(text)
  if (ok) {
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 1500)
  } else {
    ElMessage.error('复制失败，请手动复制')
  }
}

// 回答正文中内联图片放大预览（事件委托）
const previewUrl = ref<string | null>(null)

function onBodyClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (target.tagName === 'IMG') {
    const src = (target as HTMLImageElement).getAttribute('src')
    if (src) previewUrl.value = src
  }
}

function like() {
  emit('feedback', props.message, 1, '')
}

function dislike() {
  showCommentFor.value = showCommentFor.value === -1 ? 0 : -1
}

function submitDislike() {
  emit('feedback', props.message, -1, comment.value)
  showCommentFor.value = 0
  comment.value = ''
}
</script>

<template>
  <!-- 用户消息 -->
  <div v-if="message.role === 'user'" class="msg-enter flex justify-end">
    <div class="flex max-w-[78%] items-start gap-2.5">
      <div class="rounded-2xl rounded-tr-sm bg-gradient-to-br from-primary to-primary-dark px-4 py-2.5 text-[14px] leading-6 text-white shadow-lift">
        {{ message.content }}
      </div>
      <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <User :size="15" />
      </div>
    </div>
  </div>

  <!-- AI 消息 -->
  <div v-else class="msg-enter flex justify-start">
    <div class="w-full max-w-[85%]">
      <div class="glass rounded-2xl rounded-tl-sm px-4 py-3.5">
        <StepPanel :steps="message.steps" :streaming="message.streaming" />

        <div v-if="message.reasoning" class="mb-2 rounded-lg border border-muted/60 bg-muted/30">
          <button
            class="flex w-full items-center gap-1.5 px-3 py-2 text-[12px] font-medium text-ink-sub transition-colors hover:text-ink cursor-pointer"
            @click="showReasoning = !showReasoning"
          >
            <Brain :size="13" class="text-primary/70" />
            <span>思考过程</span>
            <span v-if="message.streaming" class="text-[11px] text-ink-sub/50">生成中…</span>
            <ChevronDown
              :size="13"
              class="ml-auto transition-transform duration-200"
              :class="{ 'rotate-180': showReasoning }"
            />
          </button>
          <div
            v-if="showReasoning"
            class="whitespace-pre-wrap border-t border-muted/60 px-3 py-2 text-[12px] leading-5 text-ink-sub/80"
          >
            {{ message.reasoning }}
          </div>
        </div>

        <div
          class="md-body"
          :class="{ 'stream-cursor': message.streaming }"
          v-html="rendered"
          @click="onBodyClick"
        />
        <div v-if="message.streaming && !message.content" class="flex items-center gap-1.5 py-1">
          <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-primary/60" />
          <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-primary/60" style="animation-delay: 0.15s" />
          <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-primary/60" style="animation-delay: 0.3s" />
        </div>

        <ClarifyCard
          v-if="message.clarify"
          :clarify="message.clarify"
          :disabled="generating"
          @submit="(a) => emit('clarifySubmit', a)"
        />

        <SourceCard v-if="!message.streaming" :sources="message.sources" />
      </div>

      <!-- 操作条：复制 / 删除（整组问答） / 反馈 -->
      <div
        v-if="!message.streaming && message.content"
        class="mt-1.5 flex items-center gap-1 pl-1"
      >
        <button
          class="rounded-md p-1.5 transition-all duration-200 hover:bg-muted cursor-pointer"
          :class="copied ? 'text-success' : 'text-ink-sub/60 hover:text-ink'"
          :title="copied ? '已复制' : '复制回答'"
          @click="copy"
        >
          <Check v-if="copied" :size="14" />
          <Copy v-else :size="14" />
        </button>
        <button
          class="rounded-md p-1.5 transition-all duration-200 hover:bg-danger/10 text-ink-sub/60 hover:text-danger cursor-pointer"
          title="删除这组问答"
          @click="emit('delete', message)"
        >
          <Trash2 :size="14" />
        </button>
        <template v-if="!message.clarify">
          <button
            class="rounded-md p-1.5 transition-all duration-200 hover:bg-success/10 cursor-pointer"
            :class="message.feedback === 1 ? 'text-success' : 'text-ink-sub/60 hover:text-success'"
            title="有帮助"
            @click="like"
          >
            <ThumbsUp :size="14" :fill="message.feedback === 1 ? 'currentColor' : 'none'" />
          </button>
          <button
            class="rounded-md p-1.5 transition-all duration-200 hover:bg-danger/10 cursor-pointer"
            :class="message.feedback === -1 ? 'text-danger' : 'text-ink-sub/60 hover:text-danger'"
            title="没帮助"
            @click="dislike"
          >
            <ThumbsDown :size="14" :fill="message.feedback === -1 ? 'currentColor' : 'none'" />
          </button>
          <span v-if="message.feedback !== 0" class="text-[11px] text-ink-sub/60">感谢反馈</span>
        </template>
      </div>

      <!-- 踩：评语输入 -->
      <div v-if="showCommentFor === -1" class="msg-enter mt-1.5 flex items-center gap-2 pl-1">
        <input
          v-model="comment"
          type="text"
          placeholder="哪里不准确？（可选）"
          class="h-8 flex-1 rounded-lg border border-muted bg-white px-3 text-[12px] outline-none focus:border-danger/50 focus:ring-2 focus:ring-danger/10"
          @keydown.enter="submitDislike"
        />
        <button
          class="h-8 rounded-lg bg-danger/90 px-3 text-[12px] text-white transition-all hover:bg-danger cursor-pointer"
          @click="submitDislike"
        >
          提交
        </button>
      </div>
    </div>
  </div>

  <!-- 回答正文内联图片放大预览 -->
  <Teleport to="body">
    <div
      v-if="previewUrl"
      class="fixed inset-0 z-[60] flex cursor-zoom-out items-center justify-center bg-black/70 backdrop-blur-sm"
      @click="previewUrl = null"
    >
      <img :src="previewUrl" class="max-h-[85vh] max-w-[90vw] rounded-xl shadow-2xl" />
    </div>
  </Teleport>
</template>
