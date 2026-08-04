<script setup lang="ts">
/** 过程步骤面板：意图识别 / 检索 / 工具调用 / 思考 时间线 */
import { Brain, ChevronDown, Search, Sparkles, Wrench } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import type { IntentResult, StepEvent } from '../types'

const props = defineProps<{ steps: StepEvent[]; streaming?: boolean }>()
const expanded = ref(false)

// 流式生成时自动展开，完成后自动收起
watch(
  () => props.steps.length,
  (len, oldLen) => {
    if (len > 0 && len > (oldLen ?? 0)) {
      expanded.value = true
    }
  },
)

watch(
  () => props.streaming,
  (val) => {
    if (!val && props.steps.length > 0) {
      // 完成后延迟收起，让用户有时间查看
      setTimeout(() => {
        expanded.value = false
      }, 2000)
    }
  },
)

const icons: Record<string, any> = {
  intent: Brain,
  retrieve: Search,
  thinking: Sparkles,
  tool_call: Wrench,
}

const intentSteps = computed(() =>
  props.steps.filter((s) => s.type === 'intent' && s.detail?.intents),
)

function intentBadge(t: IntentResult['intent_type']) {
  return {
    precise: { text: '精确', cls: 'bg-success/10 text-success' },
    vague: { text: '模糊', cls: 'bg-warning/10 text-warning' },
    irrelevant: { text: '不相关', cls: 'bg-danger/10 text-danger' },
  }[t]
}

function hits(step: StepEvent) {
  return (step.detail?.hits ?? []) as Array<{
    doc: string
    path: string
    score: number
  }>
}
</script>

<template>
  <div v-if="steps.length" class="mb-3">
    <button
      class="flex items-center gap-1.5 text-[12px] text-ink-sub hover:text-primary transition-colors cursor-pointer"
      @click="expanded = !expanded"
    >
      <Sparkles :size="13" class="text-primary" />
      <span>思考过程（{{ steps.length }} 步）</span>
      <ChevronDown
        :size="13"
        class="transition-transform duration-200"
        :class="{ 'rotate-180': expanded }"
      />
    </button>

    <div v-show="expanded" class="mt-2 ml-1.5 border-l-2 border-primary/15 pl-4 space-y-3">
      <div v-for="(s, i) in steps" :key="i" class="relative">
        <span class="absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full bg-primary/25 ring-2 ring-white" />
        <div class="flex items-center gap-1.5 text-[12px] font-medium text-ink">
          <component :is="icons[s.type] ?? Sparkles" :size="13" class="text-primary" />
          {{ s.title }}
        </div>

        <!-- 意图识别明细 -->
        <div v-if="s.detail?.intents" class="mt-1.5 space-y-1.5">
          <div
            v-for="(it, j) in s.detail.intents as IntentResult[]"
            :key="j"
            class="rounded-lg bg-muted/70 px-2.5 py-2 text-[12px]"
          >
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-ink">{{ it.simple_input || it.input }}</span>
              <span
                class="rounded-full px-1.5 py-0.5 text-[11px] font-medium"
                :class="intentBadge(it.intent_type).cls"
              >
                {{ intentBadge(it.intent_type).text }}
              </span>
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
        <div v-else-if="hits(s).length" class="mt-1.5 space-y-1">
          <div
            v-for="(h, j) in hits(s)"
            :key="j"
            class="flex items-center gap-2 rounded-lg bg-muted/70 px-2.5 py-1.5 text-[12px]"
          >
            <span class="truncate text-ink">{{ h.doc }} &gt; {{ h.path }}</span>
            <span class="ml-auto flex items-center gap-1.5 shrink-0">
              <span class="h-1.5 w-16 overflow-hidden rounded-full bg-white">
                <span
                  class="block h-full rounded-full bg-gradient-to-r from-primary-light to-primary"
                  :style="{ width: `${Math.min(100, h.score * 100)}%` }"
                />
              </span>
              <span class="text-ink-sub">{{ h.score.toFixed(3) }}</span>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 收起时显示意图徽标摘要 -->
    <div v-if="!expanded && intentSteps.length" class="mt-1.5 flex gap-1.5 flex-wrap">
      <template v-for="(s, i) in intentSteps" :key="i">
        <span
          v-for="(it, j) in s.detail.intents as IntentResult[]"
          :key="j"
          class="rounded-full px-2 py-0.5 text-[11px] font-medium"
          :class="intentBadge(it.intent_type).cls"
        >
          {{ it.simple_input || it.input }} · {{ intentBadge(it.intent_type).text }}
        </span>
      </template>
    </div>
  </div>
</template>
