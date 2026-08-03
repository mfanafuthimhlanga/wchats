let _jwt = null
export async function loadConfig(apiBase, agentId) {
  const res = await fetch(`${apiBase}/widget/${agentId}/config`)
  const data = await res.json()
  _jwt = data.jwt
  return data
}
export async function sendChat(apiBase, agentId, message, conversationId) {
  const res = await fetch(`${apiBase}/widget/${agentId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${_jwt}` },
    body: JSON.stringify({ message, conversation_id: conversationId })
  })
  if (res.status === 401) throw new Error('JWT expired')
  return res.json()
}
export async function sendFeedback(apiBase, agentId, messageId, conversationId, rating, score) {
  const body = { message_id: messageId, conversation_id: conversationId, rating }
  if (score != null) body.csat_score = score
  const res = await fetch(`${apiBase}/widget/agents/${agentId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${_jwt}` },
    body: JSON.stringify(body)
  })
  // Do not parse the response body here: the route answers 204 with no
  // content (apps/api/app/api/v1/widget.py:760-763). Parsing it as JSON
  // would throw on the success path — the request that worked, wrote the
  // row, and then the widget reverted its own control anyway.
  return res
}
export function getJwt() { return _jwt }
