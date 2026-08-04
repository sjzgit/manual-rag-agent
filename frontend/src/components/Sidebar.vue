<script setup lang="ts">
/** 会话侧边栏 */
import { MessageSquare, PanelLeftClose, PanelLeftOpen, Plus } from 'lucide-vue-next'
import { ref } from 'vue'
import type { SessionItem } from '../types'

defineProps<{
  sessions: SessionItem[]
  currentId: string
}>()
const emit = defineEmits<{
  create: []
  open: [id: string]
}>()

const collapsed = ref(false)

function fmtTime(iso: string) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
</script>

<template>
  <aside
    class="glass flex shrink-0 flex-col transition-all duration-300 ease-in-out"
    :class="collapsed ? 'w-14' : 'w-64'"
  >
    <div class="flex items-center justify-between p-3">
      <button
        v-if="!collapsed"
        class="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl bg-gradient-to-r from-primary to-primary-light text-[13px] font-medium text-white shadow-lift transition-all duration-200 hover:brightness-110 active:scale-[0.98] cursor-pointer"
        @click="emit('create')"
      >
        <Plus :size="15" />
        新建会话
      </button>
      <button
        v-else
        class="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-r from-primary to-primary-light text-white shadow-lift transition-all hover:brightness-110 cursor-pointer"
        @click="emit('create')"
      >
        <Plus :size="15" />
      </button>
      <button
        v-if="!collapsed"
        class="ml-2 flex h-8 w-8 items-center justify-center rounded-lg text-ink-sub transition-colors hover:bg-muted hover:text-ink cursor-pointer"
        @click="collapsed = true"
      >
        <PanelLeftClose :size="15" />
      </button>
    </div>

    <button
      v-if="collapsed"
      class="mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-lg text-ink-sub transition-colors hover:bg-muted hover:text-ink cursor-pointer"
      @click="collapsed = false"
    >
      <PanelLeftOpen :size="15" />
    </button>

    <div v-if="!collapsed" class="flex-1 space-y-1 overflow-y-auto px-2.5 pb-3">
      <button
        v-for="s in sessions"
        :key="s.id"
        class="group flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left transition-all duration-200 cursor-pointer"
        :class="
          s.id === currentId
            ? 'bg-primary/10 text-primary'
            : 'text-ink hover:bg-muted'
        "
        @click="emit('open', s.id)"
      >
        <MessageSquare :size="14" class="shrink-0 opacity-60" />
        <div class="min-w-0 flex-1">
          <div class="truncate text-[13px]">{{ s.title || '新会话' }}</div>
          <div class="text-[11px] text-ink-sub/70">{{ fmtTime(s.updated_at) }}</div>
        </div>
      </button>
      <div v-if="!sessions.length" class="px-3 py-6 text-center text-[12px] text-ink-sub/60">
        暂无会话，点击上方按钮开始
      </div>
    </div>
  </aside>
</template>
