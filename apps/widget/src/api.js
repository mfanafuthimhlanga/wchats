let _jwt = null
export async function loadConfig(apiBase, agentId) {
  const res = await fetch(`${apiBase}/widget/${agentId}/config`)
  // A 429 or a 500 still parses as JSON — the route answers both with a
  // {"detail": ...} body. Treating that as a mint set _jwt to undefined and
  // poisoned the module for the rest of the session: every later sendChat AND
  // sendFeedback went out as `Bearer undefined`, so each user message cost
  // three requests and drove the caller INTO the config route's 10/min per-IP
  // limit instead of backing off. Refuse the response instead of storing it.
  if (!res.ok) {
    throw new Error(`config request failed (${res.status})`)
  }
  const data = await res.json()
  if (!data || !data.jwt) {
    throw new Error('config response carried no jwt')
  }
  _jwt = data.jwt
  return data
}
export async function sendChat(apiBase, agentId, message, conversationId) {
  const post = () => fetch(`${apiBase}/widget/${agentId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${_jwt}` },
    body: JSON.stringify({ message, conversation_id: conversationId })
  })
  let res = await post()
  // The config route mints a JWT that expires 900s later
  // (apps/api/app/api/v1/widget.py). A customer who leaves the widget open and
  // comes back with a follow-up question spends the rest of the session on 401s,
  // because the token only ever arrived once. Re-mint and resend ONCE: a 401 on
  // the retry is a real auth failure, and a loop would run into the config
  // route's 10 req/min per-IP limit.
  if (res.status === 401) {
    try {
      await loadConfig(apiBase, agentId)
    } catch (err) {
      // Surface why the re-mint failed. A 429 here is a rate limit, not an
      // expiry, and reporting every one of them as "JWT expired" sent the
      // reader looking at token lifetimes for a throttling problem.
      throw new Error(`re-mint failed: ${err.message}`)
    }
    res = await post()
    if (res.status === 401) throw new Error('JWT expired')
  }
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
