/** SSE 流式客户端与会话/反馈 API 封装 */
import type { ChatMessage, SessionItem } from '../types'

const BASE = ''

export interface StreamHandlers {
  onMeta?: (sessionId: string, messageId: string) => void
  onStep?: (step: ChatMessage['steps'][number]) => void
  onClarify?: (payload: NonNullable<ChatMessage['clarify']>) => void
  onToken?: (text: string) => void
  onSources?: (sources: ChatMessage['sources']) => void
  onDone?: (messageId: string) => void
  onError?: (code: string, message: string) => void
}

export async function streamChat(
  body: { session_id?: string; question: string; doc?: string | null; clarify_answer?: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || !resp.body) {
    handlers.onError?.('HTTP_ERROR', `请求失败：${resp.status}`)
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const dispatch = (raw: string) => {
    const lines = raw.split('\n')
    let event = ''
    let dataStr = ''
    for (const line of lines) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataStr = line.slice(5).trim()
    }
    if (!event || !dataStr) return
    let data: any
    try {
      data = JSON.parse(dataStr)
    } catch {
      console.error('SSE 数据解析失败', dataStr)
      return
    }
    switch (event) {
      case 'meta':
        handlers.onMeta?.(data.session_id, data.message_id)
        break
      case 'step':
        handlers.onStep?.(data)
        break
      case 'clarify':
        handlers.onClarify?.(data)
        break
      case 'token':
        handlers.onToken?.(data.text)
        break
      case 'sources':
        handlers.onSources?.(data.sources)
        break
      case 'done':
        handlers.onDone?.(data.message_id)
        break
      case 'error':
        handlers.onError?.(data.code, data.message)
        break
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        if (part.trim()) dispatch(part)
      }
    }
    if (buffer.trim()) dispatch(buffer)
  } catch (e) {
    if ((e as Error).name !== 'AbortError') {
      console.error('SSE 读取异常', e)
      handlers.onError?.('STREAM_ERROR', '连接中断')
    }
  }
}

export async function createSession(): Promise<string> {
  const resp = await fetch(`${BASE}/sessions`, { method: 'POST' })
  const data = await resp.json()
  return data.session_id
}

export async function listSessions(): Promise<SessionItem[]> {
  const resp = await fetch(`${BASE}/sessions`)
  const data = await resp.json()
  return data.sessions ?? []
}

export async function getMessages(sessionId: string): Promise<any[]> {
  const resp = await fetch(`${BASE}/sessions/${sessionId}/messages`)
  const data = await resp.json()
  return data.messages ?? []
}

export async function submitFeedback(
  messageId: string,
  score: 1 | -1,
  comment = '',
): Promise<boolean> {
  try {
    const resp = await fetch(`${BASE}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message_id: messageId, score, comment }),
    })
    return resp.ok
  } catch (e) {
    console.error('反馈提交失败', e)
    return false
  }
}
