import { h } from 'preact'
import { useState, useEffect } from 'preact/hooks'
import { loadConfig, sendChat } from './api.js'
import { startSSEStream } from './sse.js'
import { DisclosureBar } from './components/DisclosureBar.jsx'
import { MessageBubble } from './components/MessageBubble.jsx'
import { CitationRow } from './components/CitationRow.jsx'
import { TypingIndicator } from './components/TypingIndicator.jsx'
import { ToolCallLabel } from './components/ToolCallLabel.jsx'
import { EscalationPanel } from './components/EscalationPanel.jsx'
import { InputBar } from './components/InputBar.jsx'

export function Widget({ agentId, apiBase }) {
  const [messages, setMessages] = useState([])
  const [status, setStatus] = useState('loading')
  const [toolCallText, setToolCallText] = useState('')
  const [escalation, setEscalation] = useState(null)
  const [conversationId, setConversationId] = useState(null)

  useEffect(() => {
    loadConfig(apiBase, agentId).then(cfg => {
      Object.entries(cfg.theming).forEach(([k, v]) =>
        document.documentElement.style.setProperty(`--${k.replace(/_/g, '-')}`, v))
      setStatus('idle')
    }).catch(() => setStatus('error'))
  }, [agentId, apiBase])

  const handleSend = async (text) => {
    setStatus('submitting')
    setMessages(m => [...m, { role: 'user', text }])
    try {
      const { job_id, conversation_id: cid } = await sendChat(apiBase, agentId, text, conversationId)
      if (cid) setConversationId(cid)
      setStatus('thinking')
      startSSEStream(apiBase, job_id, {
        onThinking: () => setStatus('thinking'),
        onToolCall: (p) => { setStatus('tool_call'); setToolCallText(p.tool_name) },
        onToolResult: () => setStatus('thinking'),
        onResponse: (p) => {
          setMessages(m => [...m, { role: 'agent', text: p.text, citations: p.citations }])
          if (p.conversation_id) setConversationId(p.conversation_id)
          setStatus('idle')
        },
        onEscalated: (p) => { setEscalation(p); setStatus('escalated') },
        onError: () => setStatus('error'),
        onFailed: () => setStatus('error')
      })
    } catch { setStatus('error') }
  }

  const disabled = ['thinking', 'tool_call', 'submitting'].includes(status)

  return (
    <div class="widget-root">
      <DisclosureBar />
      <div class="scroll-area" role="log" aria-live="polite">
        {messages.map((m, i) => (
          <div key={i}>
            <MessageBubble role={m.role} text={m.text} />
            {m.role === 'agent' && <CitationRow citations={m.citations} />}
          </div>
        ))}
        {status === 'thinking' && <TypingIndicator />}
        {status === 'tool_call' && <ToolCallLabel toolName={toolCallText} input={{}} />}
        {escalation && <EscalationPanel reason={escalation.reason} context={escalation.context} onSubmit={() => {}} />}
        {status === 'error' && <div class="error-msg">Something went wrong. Please try again.</div>}
      </div>
      <InputBar disabled={disabled} onSend={handleSend} />
    </div>
  )
}
