<script setup lang="ts">
/** 消息气泡：用户右 / AI 左，Markdown 渲染 + 步骤面板 + 来源卡片 + 澄清卡片 + 反馈条 */
import MarkdownIt from 'markdown-it'
import { ThumbsDown, ThumbsUp, User, ZoomIn } from 'lucide-vue-next'
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
}>()

const md = new MarkdownIt({ html: false, linkify: true })
const rendered = computed(() => md.render(props.message.content || ''))

const showCommentFor = ref<-1 | 0>(0)
const comment = ref('')

// 从 sources 中收集所有不重复的图片用于在正文区展示
const sourceImages = computed(() => {
  if (props.message.streaming || !props.message.sources?.length) return []
  const seen = new Set<string>()
  const result: { url: string; doc: string; path: string }[] = []
  for (const s of props.message.sources) {
    for (const img of s.images) {
      if (!seen.has(img)) {
        seen.add(img)
        result.push({ url: img, doc: s.doc, path: s.path })
      }
    }
  }
  return result
})

// 图片放大预览
const previewImage = ref<string | null>(null)

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

        <div
          class="md-body"
          :class="{ 'stream-cursor': message.streaming }"
          v-html="rendered"
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

        <!-- 来源截图展示 -->
        <div v-if="sourceImages.length" class="mt-3 border-t border-muted pt-2.5">
          <div class="mb-2 text-[11px] font-medium text-ink-sub">相关操作截图</div>
          <div class="grid gap-2" :style="{ gridTemplateColumns: `repeat(${Math.min(sourceImages.length, 3)}, minmax(0, 1fr))` }">
            <button
              v-for="(img, i) in sourceImages"
              :key="i"
              class="group relative overflow-hidden rounded-lg border border-muted bg-white cursor-zoom-in shadow-sm transition-shadow hover:shadow-md"
              @click="previewImage = img.url"
            >
              <img
                :src="img.url"
                :alt="`${img.doc} - ${img.path}`"
                class="h-28 w-full object-cover transition-transform duration-300 group-hover:scale-105"
                loading="lazy"
              />
              <div class="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/50 to-transparent p-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                <span class="text-[10px] text-white/90 truncate block">{{ img.doc }}</span>
              </div>
              <div class="absolute right-1.5 top-1.5 rounded-md bg-black/40 p-1 opacity-0 transition-opacity group-hover:opacity-100">
                <ZoomIn :size="13" class="text-white" />
              </div>
            </button>
          </div>
        </div>

        <SourceCard v-if="!message.streaming" :sources="message.sources" />
      </div>

      <!-- 反馈条 -->
      <div
        v-if="!message.streaming && message.content && !message.clarify"
        class="mt-1.5 flex items-center gap-1 pl-1"
      >
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

  <!-- 图片放大预览 -->
  <Teleport to="body">
    <div
      v-if="previewImage"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm cursor-zoom-out"
      @click="previewImage = null"
    >
      <img :src="previewImage" class="max-h-[85vh] max-w-[90vw] rounded-xl shadow-2xl" />
    </div>
  </Teleport>
</template>
