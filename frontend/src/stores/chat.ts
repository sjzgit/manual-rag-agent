/** Pinia 会话与消息流状态 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createSession,
  getMessages,
  listSessions,
  streamChat,
  submitFeedback,
} from '../api/chat'
import type { ChatMessage, SessionItem } from '../types'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<SessionItem[]>([])
  const currentSessionId = ref<string>('')
  const messages = ref<ChatMessage[]>([])
  const generating = ref(false)
  const docFilter = ref<string | null>(null)
  let abortCtrl: AbortController | null = null

  async function refreshSessions() {
    sessions.value = await listSessions()
  }

  async function newSession() {
    currentSessionId.value = await createSession()
    messages.value = []
    await refreshSessions()
  }

  async function openSession(id: string) {
    currentSessionId.value = id
    const rows = await getMessages(id)
    messages.value = rows.map((r) => ({
      id: r.id,
      role: r.role,
      content: r.content,
      steps: [],
      sources: r.sources ?? [],
      streaming: false,
      feedback: 0,
    }))
  }

  async function send(question: string, clarifyAnswer?: string) {
    if (generating.value) return
    if (!currentSessionId.value) {
      currentSessionId.value = await createSession()
    }

    const display = clarifyAnswer ?? question
    messages.value.push({
      id: `u-${Date.now()}`,
      role: 'user',
      content: display,
      steps: [],
      sources: [],
      feedback: 0,
    })

    const aiId = `a-${Date.now()}`
    const aiMsg: ChatMessage = {
      id: aiId,
      role: 'assistant',
      content: '',
      steps: [],
      sources: [],
      streaming: true,
      feedback: 0,
    }
    messages.value.push(aiMsg)
    generating.value = true
    abortCtrl = new AbortController()

    // 辅助函数：从响应式数组中获取 AI 消息的 reactive proxy
    function getAIMsg(): ChatMessage | undefined {
      return messages.value.find((m) => m.id === aiId)
    }

    await streamChat(
      {
        session_id: currentSessionId.value,
        question,
        doc: docFilter.value,
        clarify_answer: clarifyAnswer,
      },
      {
        onMeta: (sid, mid) => {
          currentSessionId.value = sid
          const m = getAIMsg()
          if (m) m.id = mid
        },
        onStep: (s) => {
          const m = getAIMsg()
          if (m) m.steps.push(s)
        },
        onClarify: (c) => {
          const m = getAIMsg()
          if (m) m.clarify = c
        },
        onToken: (t) => {
          const m = getAIMsg()
          if (m) m.content += t
        },
        onSources: (s) => {
          const m = getAIMsg()
          if (m) m.sources = s
        },
        onDone: () => {
          const m = getAIMsg()
          if (m) m.streaming = false
        },
        onError: (_code, msg) => {
          const m = getAIMsg()
          if (m) {
            m.content += `\n\n> 出错了：${msg}`
            m.streaming = false
          }
        },
      },
      abortCtrl.signal,
    )

    const finalMsg = getAIMsg()
    if (finalMsg) finalMsg.streaming = false
    generating.value = false
    abortCtrl = null
    refreshSessions()
  }

  function stop() {
    abortCtrl?.abort()
    generating.value = false
    const last = messages.value[messages.value.length - 1]
    if (last?.role === 'assistant') last.streaming = false
  }

  async function feedback(msg: ChatMessage, score: 1 | -1, comment = '') {
    const ok = await submitFeedback(msg.id, score, comment)
    if (ok) msg.feedback = score
    return ok
  }

  return {
    sessions,
    currentSessionId,
    messages,
    generating,
    docFilter,
    refreshSessions,
    newSession,
    openSession,
    send,
    stop,
    feedback,
  }
})
