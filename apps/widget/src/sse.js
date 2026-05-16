export function startSSEStream(apiBase, jobId, handlers) {
  const es = new EventSource(`${apiBase}/widget/jobs/${jobId}/events`)
  es.addEventListener('agent.thinking', e => handlers.onThinking?.(JSON.parse(e.data)))
  es.addEventListener('agent.tool_call', e => handlers.onToolCall?.(JSON.parse(e.data)))
  es.addEventListener('agent.tool_result', e => handlers.onToolResult?.(JSON.parse(e.data)))
  es.addEventListener('agent.response', e => { handlers.onResponse?.(JSON.parse(e.data)); es.close() })
  es.addEventListener('agent.escalated', e => handlers.onEscalated?.(JSON.parse(e.data)))
  es.addEventListener('agent.failed', e => { handlers.onFailed?.(JSON.parse(e.data)); es.close() })
  es.onerror = () => { handlers.onError?.(); es.close() }
  return es
}
