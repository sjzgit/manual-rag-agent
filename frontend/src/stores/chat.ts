/** Pinia 会话与消息流状态 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createSession,
  deleteMessages,
  deleteSession,
  getMessages,
  listSessions,
  renameSession,
  streamChat,
  submitFeedback,
} from '../api/chat'
import type { ChatMessage, SessionItem } from '../types'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<SessionItem[]>([])
  const currentSessionId = ref<string>('')
  const messages = ref<ChatMessage[]>([])
  const generating = ref(false)
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
      steps: r.steps ?? [],
      sources: r.sources ?? [],
      reasoning: r.reasoning ?? '',
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

    messages.value.push({
      id: `a-${Date.now()}`,
      role: 'assistant',
      content: '',
      steps: [],
      sources: [],
      reasoning: '',
      streaming: true,
      feedback: 0,
    })
    // push 后立即从响应式数组中捕获该消息的 reactive proxy。
    // 注意：不能通过 id 反复 find，因为 onMeta 会用服务端真实 id 覆盖本地 id，导致后续查找失效。
    const userMsg = messages.value[messages.value.length - 2]
    const aiMsg = messages.value[messages.value.length - 1]
    generating.value = true
    abortCtrl = new AbortController()

    try {
      await streamChat(
        {
          session_id: currentSessionId.value,
          question,
          clarify_answer: clarifyAnswer,
        },
        {
          onMeta: (sid, mid) => {
            currentSessionId.value = sid
            aiMsg.id = mid
            // 后端用户消息 id 采用「assistant id + _u」确定性后缀，据此对齐真实 id
            userMsg.id = `${mid}_u`
          },
          onStep: (s) => aiMsg.steps.push(s),
          onClarify: (c) => {
            aiMsg.clarify = c
          },
          onToken: (t) => {
            aiMsg.content += t
          },
          onReasoning: (t) => {
            aiMsg.reasoning = (aiMsg.reasoning ?? '') + t
          },
          onSources: (s) => {
            aiMsg.sources = s
          },
          onDone: () => {
            aiMsg.streaming = false
          },
          onError: (_code, msg) => {
            aiMsg.content += `\n\n> 出错了：${msg}`
            aiMsg.streaming = false
          },
        },
        abortCtrl.signal,
      )
    } catch (e) {
      console.error('聊天流异常', e)
      aiMsg.content += '\n\n> 出错了：连接中断，请重试'
    } finally {
      // 无论成功/失败/中断，都必须复位生成状态，避免输入框永久禁用
      aiMsg.streaming = false
      generating.value = false
      abortCtrl = null
    }
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

  async function renameSessionLocal(id: string, title: string) {
    const ok = await renameSession(id, title)
    if (ok) {
      const s = sessions.value.find((x) => x.id === id)
      if (s) s.title = title
    }
    return ok
  }

  async function deleteSessionLocal(id: string) {
    const ok = await deleteSession(id)
    if (!ok) return false
    sessions.value = sessions.value.filter((x) => x.id !== id)
    if (currentSessionId.value === id) {
      if (sessions.value.length) {
        await openSession(sessions.value[0].id)
      } else {
        await newSession()
      }
    }
    return true
  }

  async function deleteMessage(msg: ChatMessage) {
    if (generating.value) return false
    const idx = messages.value.findIndex((m) => m.id === msg.id)
    if (idx === -1) return false
    // 按「一组问答」删除：助手消息连带其前一条用户消息，反之亦然
    const ids = [msg.id]
    if (msg.role === 'assistant') {
      for (let i = idx - 1; i >= 0; i--) {
        if (messages.value[i].role === 'user') {
          ids.push(messages.value[i].id)
          break
        }
      }
    } else {
      for (let i = idx + 1; i < messages.value.length; i++) {
        if (messages.value[i].role === 'assistant') {
          ids.push(messages.value[i].id)
          break
        }
      }
    }
    const ok = await deleteMessages(currentSessionId.value, ids)
    if (ok) {
      const set = new Set(ids)
      messages.value = messages.value.filter((m) => !set.has(m.id))
    }
    return ok
  }

  return {
    sessions,
    currentSessionId,
    messages,
    generating,
    refreshSessions,
    newSession,
    openSession,
    send,
    stop,
    feedback,
    renameSession: renameSessionLocal,
    deleteSession: deleteSessionLocal,
    deleteMessage,
  }
})
