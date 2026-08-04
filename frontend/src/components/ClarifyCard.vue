<script setup lang="ts">
/** 澄清询问卡片：内嵌消息流，快捷选项 + 输入框 */
import { HelpCircle, SendHorizonal } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { ClarifyPayload } from '../types'

const props = defineProps<{
  clarify: ClarifyPayload
  disabled?: boolean
}>()
const emit = defineEmits<{ submit: [answer: string] }>()

const input = ref('')

const fieldLabels: Record<string, string> = {
  module: '功能模块',
  role: '用户角色',
  description: '功能点',
}

const fields = computed(() =>
  props.clarify.missing_fields.map((f) => fieldLabels[f] ?? f),
)

// 从意图的候选切片中提取快捷选项（手册名）
const options = computed(() => {
  const docs = new Set<string>()
  for (const it of props.clarify.intents) {
    if (it.module) docs.add(it.module)
  }
  return [...docs].slice(0, 5)
})

function pick(opt: string) {
  input.value = opt
  submit()
}

function submit() {
  const v = input.value.trim()
  if (!v || props.disabled) return
  emit('submit', v)
  input.value = ''
}
</script>

<template>
  <div class="msg-enter mt-3 rounded-xl border border-warning/30 bg-warning/5 p-3.5">
    <div class="flex items-start gap-2">
      <HelpCircle :size="16" class="mt-0.5 shrink-0 text-warning" />
      <div class="flex-1">
        <div class="text-[13px] font-medium text-ink">{{ clarify.question }}</div>
        <div class="mt-1 flex flex-wrap gap-1 text-[11px] text-warning/90">
          <span>待补充：</span>
          <span
            v-for="f in fields"
            :key="f"
            class="rounded-full bg-warning/10 px-1.5 py-0.5"
          >
            {{ f }}
          </span>
        </div>

        <div v-if="options.length" class="mt-2 flex flex-wrap gap-1.5">
          <button
            v-for="opt in options"
            :key="opt"
            class="rounded-full border border-warning/30 bg-white px-2.5 py-1 text-[12px] text-ink transition-all duration-200 hover:border-warning hover:text-warning hover:shadow-lift cursor-pointer"
            @click="pick(opt)"
          >
            {{ opt }}
          </button>
        </div>

        <div class="mt-2.5 flex items-center gap-2">
          <input
            v-model="input"
            type="text"
            placeholder="请输入补充说明…"
            :disabled="disabled"
            class="h-9 flex-1 rounded-lg border border-warning/25 bg-white px-3 text-[13px] outline-none transition-shadow focus:border-warning/60 focus:ring-2 focus:ring-warning/15 disabled:opacity-50"
            @keydown.enter="submit"
          />
          <button
            class="flex h-9 w-9 items-center justify-center rounded-lg bg-warning text-white transition-all duration-200 hover:brightness-110 active:scale-95 disabled:opacity-50 cursor-pointer"
            :disabled="disabled || !input.trim()"
            @click="submit"
          >
            <SendHorizonal :size="15" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
