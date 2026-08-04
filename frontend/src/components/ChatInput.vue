<script setup lang="ts">
/** 底部输入区：多行输入 + 发送/停止 */
import { SendHorizonal, Square } from 'lucide-vue-next'
import { ref } from 'vue'

const props = defineProps<{ generating: boolean }>()
const emit = defineEmits<{
  send: [text: string]
  stop: []
}>()

const text = ref('')
const textarea = ref<HTMLTextAreaElement>()

function autoResize() {
  const el = textarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

function submit() {
  const v = text.value.trim()
  if (!v || props.generating) return
  // 先清空输入框，再触发发送，避免发送流程异常时文字残留
  text.value = ''
  autoResize()
  emit('send', v)
}
</script>

<template>
  <div class="glass rounded-2xl p-2.5">
    <div class="flex items-end gap-2">
      <textarea
        ref="textarea"
        v-model="text"
        rows="1"
        placeholder="请输入您想咨询的操作问题，Enter 发送 / Shift+Enter 换行"
        :disabled="generating"
        class="max-h-40 flex-1 resize-none bg-transparent px-3 py-2.5 text-[14px] leading-6 outline-none placeholder:text-ink-sub/50 disabled:opacity-60"
        @input="autoResize"
        @keydown="onKeydown"
      />
      <button
        v-if="!generating"
        class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-primary-dark text-white shadow-lift transition-all duration-200 hover:brightness-110 hover:scale-105 active:scale-95 disabled:opacity-40 disabled:hover:scale-100 cursor-pointer"
        :disabled="!text.trim()"
        @click="submit"
      >
        <SendHorizonal :size="17" />
      </button>
      <button
        v-else
        class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-ink text-white transition-all duration-200 hover:bg-ink/85 active:scale-95 cursor-pointer"
        title="停止生成"
        @click="emit('stop')"
      >
        <Square :size="14" fill="currentColor" />
      </button>
    </div>
  </div>
</template>
