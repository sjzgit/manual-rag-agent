<script setup lang="ts">
/** 管理端布局：左侧菜单 + 功能区 */
import { BookOpen, Cpu, Inbox, MessageSquare } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const menus = [
  { path: '/admin/knowledge', label: '知识库管理', icon: BookOpen },
  { path: '/admin/prompt', label: '提示词管理', icon: Cpu },
  { path: '/admin/session', label: '会话管理', icon: MessageSquare },
  { path: '/admin/feedback', label: '用户反馈', icon: Inbox },
]

function go(path: string) {
  router.push(path)
}
</script>

<template>
  <div class="flex h-full">
    <!-- 侧边栏 -->
    <aside class="flex w-56 shrink-0 flex-col border-r border-muted bg-white/70 backdrop-blur-xl">
      <div class="flex h-14 items-center gap-2.5 border-b border-muted px-5">
        <div class="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-primary-dark text-white">
          <BookOpen :size="16" />
        </div>
        <div class="text-[14px] font-semibold text-ink">管理系统</div>
      </div>
      <nav class="flex-1 p-3">
        <button
          v-for="m in menus"
          :key="m.path"
          class="mb-1 flex w-full cursor-pointer items-center gap-2.5 rounded-xl px-3 py-2.5 text-[13px] transition-all"
          :class="
            route.path.startsWith(m.path)
              ? 'bg-primary/10 font-medium text-primary'
              : 'text-ink-sub hover:bg-muted hover:text-ink'
          "
          @click="go(m.path)"
        >
          <component :is="m.icon" :size="16" />
          <span>{{ m.label }}</span>
        </button>
      </nav>
      <div class="border-t border-muted p-3">
        <button
          class="flex w-full cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-[12px] text-ink-sub transition-colors hover:bg-muted hover:text-ink"
          @click="router.push('/')"
        >
          <MessageSquare :size="14" />
          <span>返回 C 端</span>
        </button>
      </div>
    </aside>

    <!-- 功能区 -->
    <main class="min-w-0 flex-1 overflow-y-auto p-5">
      <router-view />
    </main>
  </div>
</template>
