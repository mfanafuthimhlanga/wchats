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
export function getJwt() { return _jwt }
