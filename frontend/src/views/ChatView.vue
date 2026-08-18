<script setup lang="ts">
/** 聊天主界面 */
import { ElMessageBox } from 'element-plus'
import { GraduationCap, Settings } from 'lucide-vue-next'
import { nextTick, onMounted, ref, watch } from 'vue'
import ChatInput from '../components/ChatInput.vue'
import MessageBubble from '../components/MessageBubble.vue'
import Sidebar from '../components/Sidebar.vue'
import { useChatStore } from '../stores/chat'
import type { ChatMessage } from '../types'

const store = useChatStore()
const scrollBox = ref<HTMLElement>()

onMounted(async () => {
  await store.refreshSessions()
})

watch(
  () => store.messages.length && store.messages[store.messages.length - 1]?.content,
  async () => {
    await nextTick()
    scrollBox.value?.scrollTo({ top: scrollBox.value.scrollHeight, behavior: 'smooth' })
  },
)

function onClarifySubmit(answer: string) {
  store.send('', answer)
}

function onFeedback(msg: ChatMessage, score: 1 | -1, comment: string) {
  store.feedback(msg, score, comment)
}

async function onMessageDelete(msg: ChatMessage) {
  try {
    await ElMessageBox.confirm('将同时删除该提问与对应回答，且无法恢复。', '删除这组消息', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await store.deleteMessage(msg)
  } catch {
    /* 用户取消 */
  }
}

function openAdmin() {
  window.open('/admin', '_blank')
}
</script>

<template>
  <div class="flex h-full gap-3 p-3">
    <Sidebar
      :sessions="store.sessions"
      :current-id="store.currentSessionId"
      @create="store.newSession()"
      @open="store.openSession($event)"
      @rename="(id, title) => store.renameSession(id, title)"
      @delete="store.deleteSession($event)"
    />

    <main class="flex min-w-0 flex-1 flex-col gap-3">
      <!-- 顶部导航 -->
      <header class="glass flex h-14 shrink-0 items-center gap-3 rounded-2xl px-4">
        <div class="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-primary-dark text-white shadow-lift">
          <GraduationCap :size="18" />
        </div>
        <div>
          <div class="text-[15px] font-semibold leading-5">操作手册智能助手</div>
          <div class="text-[11px] text-ink-sub">基于操作手册知识库的智能问答</div>
        </div>

        <div class="ml-auto flex items-center gap-2">
          <button
            class="flex h-9 cursor-pointer items-center gap-1.5 rounded-xl border border-muted bg-white/80 px-3 text-[13px] text-ink transition-all hover:border-primary/40 hover:text-primary"
            @click="openAdmin"
          >
            <Settings :size="14" />
            <span>管理系统</span>
          </button>

          <div class="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-primary/15 to-primary/5 text-[12px] font-semibold text-primary">
            用
          </div>
        </div>
      </header>

      <!-- 消息区 -->
      <div ref="scrollBox" class="flex-1 space-y-5 overflow-y-auto rounded-2xl px-4 py-4">
        <div
          v-if="!store.messages.length"
          class="flex h-full flex-col items-center justify-center gap-3 text-center"
        >
          <div class="flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-primary to-primary-dark text-white shadow-lift">
            <GraduationCap :size="30" />
          </div>
          <div class="text-[17px] font-semibold">有什么操作问题想问？</div>
          <div class="max-w-md text-[13px] leading-6 text-ink-sub">
            例如：「如何新增会议室预约申请」「学生怎么进行签到签退」<br />
            我会先识别您的问题意图，再从操作手册中检索答案并附来源截图。
          </div>
        </div>

        <MessageBubble
          v-for="m in store.messages"
          :key="m.id"
          :message="m"
          :generating="store.generating"
          @clarify-submit="onClarifySubmit"
          @feedback="onFeedback"
          @delete="onMessageDelete"
        />
      </div>

      <!-- 输入区 -->
      <ChatInput
        :generating="store.generating"
        @send="store.send($event)"
        @stop="store.stop()"
      />
    </main>
  </div>
</template>
