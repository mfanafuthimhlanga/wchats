import { h } from 'preact'
import { useState, useEffect, useRef } from 'preact/hooks'
import { loadConfig, sendChat } from './api.js'
import { applyTheming } from './theming.js'
import { startSSEStream } from './sse.js'
import { DisclosureBar } from './components/DisclosureBar.jsx'
import { MessageBubble } from './components/MessageBubble.jsx'
import { CitationRow } from './components/CitationRow.jsx'
import { TypingIndicator } from './components/TypingIndicator.jsx'
import { ToolCallLabel } from './components/ToolCallLabel.jsx'
import { EscalationPanel } from './components/EscalationPanel.jsx'
import { InputBar } from './components/InputBar.jsx'
import { AgentCluster } from './components/AgentCluster.jsx'
import { UserMeta } from './components/UserMeta.jsx'
import { EmptyState } from './components/EmptyState.jsx'
import { FeedbackRow } from './components/FeedbackRow.jsx'

export function Widget({ agentId, apiBase }) {
  const [messages, setMessages] = useState([])
  const [status, setStatus] = useState('idle')
  const [toolCallText, setToolCallText] = useState('')
  const [escalation, setEscalation] = useState(null)
  const [conversationId, setConversationId] = useState(null)
  const [agentName, setAgentName] = useState('')
  const [sendError, setSendError] = useState(false)
  const scrollRef = useRef(null)

  useEffect(() => {
    loadConfig(apiBase, agentId).then(cfg => {
      applyTheming(cfg.theming, document.documentElement)
      if (cfg.agent_name) setAgentName(cfg.agent_name)
    }).catch(() => {
      // Config load failure is non-fatal — widget stays in idle with default greeting
    })
  }, [agentId, apiBase])

  // Auto-scroll to bottom when messages or status change
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, status])

  const handleSend = async (text) => {
    setSendError(false)
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
          setMessages(m => [...m, { role: 'agent', text: p.text, citations: p.citations, message_id: p.message_id }])
          if (p.conversation_id) setConversationId(p.conversation_id)
          setStatus('idle')
        },
        onEscalated: (p) => { setEscalation(p); setStatus('escalated') },
        onError: () => { setSendError(true); setStatus('idle') },
        onFailed: () => { setSendError(true); setStatus('idle') }
      })
    } catch { setSendError(true); setStatus('idle') }
  }

  const disabled = ['thinking', 'tool_call', 'submitting'].includes(status)
  const submitting = status === 'submitting'

  return (
    <div class="widget-root">
      <DisclosureBar />
      <div class="scroll-area" role="log" aria-live="polite" ref={scrollRef}>
        {messages.length === 0 && status !== 'submitting' && (
          <EmptyState agentName={agentName} />
        )}
        {messages.map((m, i) => (
          <div key={i}>
            {m.role === 'agent'
              ? <AgentCluster agentName={agentName}>
                  <MessageBubble role="agent" text={m.text} />
                  <CitationRow citations={m.citations} />
                  <FeedbackRow apiBase={apiBase} agentId={agentId} messageId={m.message_id} conversationId={conversationId} />
                </AgentCluster>
              : <div style="display:flex;flex-direction:column;align-items:flex-end;">
                  <MessageBubble role="user" text={m.text} />
                  <UserMeta />
                </div>
            }
          </div>
        ))}
        {(status === 'thinking' || status === 'tool_call') && <TypingIndicator />}
        {status === 'tool_call' && <ToolCallLabel toolName={toolCallText} input={{}} />}
        {sendError && (
          <div class="error-msg" role="alert">Something went wrong. Please try again.</div>
        )}
      </div>
      {escalation && (
        <EscalationPanel
          reason={escalation.reason}
          onSubmit={() => setEscalation(null)}
        />
      )}
      <InputBar disabled={disabled} submitting={submitting} onSend={handleSend} />
    </div>
  )
}
