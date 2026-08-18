<script setup lang="ts">
/** 会话侧边栏 */
import { ElMessageBox } from 'element-plus'
import {
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Trash2,
} from 'lucide-vue-next'
import { ref } from 'vue'
import type { SessionItem } from '../types'

defineProps<{
  sessions: SessionItem[]
  currentId: string
}>()
const emit = defineEmits<{
  create: []
  open: [id: string]
  rename: [id: string, title: string]
  delete: [id: string]
}>()

const collapsed = ref(false)
const editingId = ref('')
const editingTitle = ref('')

// 进入编辑态时自动聚焦输入框
const vFocus = {
  mounted: (el: HTMLInputElement) => el.focus(),
}

function fmtTime(iso: string) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function startRename(s: SessionItem) {
  editingId.value = s.id
  editingTitle.value = s.title || ''
}

function commitRename(id: string) {
  const title = editingTitle.value.trim()
  editingId.value = ''
  if (title && title !== '新会话') {
    emit('rename', id, title)
  }
}

function cancelRename() {
  editingId.value = ''
}

async function confirmDelete(s: SessionItem) {
  try {
    await ElMessageBox.confirm(
      `删除会话「${s.title || '新会话'}」后，其全部消息将无法恢复。`,
      '删除会话',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
    emit('delete', s.id)
  } catch {
    /* 用户取消 */
  }
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
      <div
        v-for="s in sessions"
        :key="s.id"
        class="group flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left transition-all duration-200 cursor-pointer"
        :class="
          s.id === currentId
            ? 'bg-primary/10'
            : 'hover:bg-muted'
        "
        @click="emit('open', s.id)"
      >
        <MessageSquare
          :size="14"
          class="shrink-0 opacity-60"
          :class="s.id === currentId ? 'text-primary' : ''"
        />
        <div class="min-w-0 flex-1">
          <input
            v-if="editingId === s.id"
            v-model="editingTitle"
            v-focus
            type="text"
            class="w-full rounded-md border border-primary/40 bg-white px-1.5 py-0.5 text-[13px] text-ink outline-none focus:ring-2 focus:ring-primary/10"
            @click.stop
            @keydown.enter="commitRename(s.id)"
            @keydown.esc="cancelRename"
            @blur="commitRename(s.id)"
          />
          <div
            v-else
            class="truncate text-[13px]"
            :class="s.id === currentId ? 'text-primary' : 'text-ink'"
          >
            {{ s.title || '新会话' }}
          </div>
          <div class="text-[11px] text-ink-sub/70">{{ fmtTime(s.updated_at) }}</div>
        </div>

        <div
          v-if="editingId !== s.id"
          class="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100"
        >
          <button
            class="rounded-md p-1 text-ink-sub/60 transition-colors hover:bg-white hover:text-primary cursor-pointer"
            title="重命名"
            @click.stop="startRename(s)"
          >
            <Pencil :size="13" />
          </button>
          <button
            class="rounded-md p-1 text-ink-sub/60 transition-colors hover:bg-white hover:text-danger cursor-pointer"
            title="删除会话"
            @click.stop="confirmDelete(s)"
          >
            <Trash2 :size="13" />
          </button>
        </div>
      </div>
      <div v-if="!sessions.length" class="px-3 py-6 text-center text-[12px] text-ink-sub/60">
        暂无会话，点击上方按钮开始
      </div>
    </div>
  </aside>
</template>
