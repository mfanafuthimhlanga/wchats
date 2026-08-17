// First tests in this workspace. No DOM and no framework rendering: api.js
// talks to fetch and nothing else, so a fake fetch is the whole harness.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getJwt, loadConfig, sendChat } from './api.js'

const API = 'https://api.test'
const AGENT = 'agent-1'

let calls
let chatStatuses // status code for each successive POST /chat
let configJwts // jwt for each successive GET /config. 'FAIL' rejects the fetch;
               // a number is the HTTP status the config route answers with.

// `ok` matters: loadConfig refuses a non-OK response rather than storing its
// body as a mint (D4).
const jsonResponse = (status, body) => ({ ok: status < 400, status, json: async () => body })

beforeEach(() => {
  calls = []
  chatStatuses = []
  configJwts = []
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    calls.push({ url, auth: init.headers && init.headers.Authorization })
    if (url.endsWith('/config')) {
      const jwt = configJwts.shift()
      if (jwt === 'FAIL') throw new TypeError('Failed to fetch')
      if (typeof jwt === 'number') return jsonResponse(jwt, { detail: 'Rate limit exceeded' })
      return jsonResponse(200, { jwt, theming: {}, agent_name: 'Ada' })
    }
    const status = chatStatuses.shift()
    return jsonResponse(status, status === 200 ? { job_id: 'job-1' } : { detail: 'expired' })
  })
})

const chatCalls = () => calls.filter((c) => c.url.endsWith('/chat'))
const configCalls = () => calls.filter((c) => c.url.endsWith('/config'))

describe('sendChat', () => {
  it('re-mints the expired JWT and resends the message once', async () => {
    configJwts = ['jwt-1', 'jwt-2']
    chatStatuses = [401, 200]
    await loadConfig(API, AGENT)

    const result = await sendChat(API, AGENT, 'still there?', null)

    expect(result).toEqual({ job_id: 'job-1' })
    expect(chatCalls().map((c) => c.auth)).toEqual(['Bearer jwt-1', 'Bearer jwt-2'])
    expect(configCalls()).toHaveLength(2)
  })

  it('gives up after one retry instead of looping on a persistent 401', async () => {
    configJwts = ['jwt-1', 'jwt-2']
    chatStatuses = [401, 401]
    await loadConfig(API, AGENT)

    await expect(sendChat(API, AGENT, 'hello', null)).rejects.toThrow('JWT expired')
    expect(chatCalls()).toHaveLength(2)
    expect(configCalls()).toHaveLength(2)
  })

  it('does not re-fetch the config when the send succeeds', async () => {
    configJwts = ['jwt-1']
    chatStatuses = [200]
    await loadConfig(API, AGENT)

    await sendChat(API, AGENT, 'hello', null)

    expect(chatCalls()).toHaveLength(1)
    expect(configCalls()).toHaveLength(1)
  })

  it('reports the failure when the re-mint fetch itself throws', async () => {
    configJwts = ['jwt-1', 'FAIL']
    chatStatuses = [401]
    await loadConfig(API, AGENT)

    await expect(sendChat(API, AGENT, 'hello', null)).rejects.toThrow('re-mint failed')
    expect(chatCalls()).toHaveLength(1)
  })

  it('keeps the old JWT when the re-mint answers 429 instead of storing undefined', async () => {
    // The gap this covers: the throwing-fetch case above was the only re-mint
    // failure tested, and a 429 does not throw — it resolves with a JSON body.
    // Storing it as a mint set _jwt to undefined, so every later request in the
    // session went out as `Bearer undefined` and each user message cost three
    // requests, accelerating toward the config route's 10/min per-IP limit.
    configJwts = ['jwt-1', 429]
    chatStatuses = [401]
    await loadConfig(API, AGENT)

    await expect(sendChat(API, AGENT, 'hello', null)).rejects.toThrow('re-mint failed: config request failed (429)')
    expect(getJwt()).toBe('jwt-1')
    expect(chatCalls()).toHaveLength(1)
  })

  it('does not poison the module for later requests after a failed re-mint', async () => {
    configJwts = ['jwt-1', 500, 'jwt-2']
    chatStatuses = [401, 200]
    await loadConfig(API, AGENT)

    await expect(sendChat(API, AGENT, 'hello', null)).rejects.toThrow('re-mint failed')

    // A later turn, once the API has recovered, still authenticates. Before the
    // fix `_jwt` was undefined from here on and every request 401'd.
    await loadConfig(API, AGENT)
    await sendChat(API, AGENT, 'still there?', null)
    expect(chatCalls().map((c) => c.auth)).toEqual(['Bearer jwt-1', 'Bearer jwt-2'])
  })
})

describe('loadConfig', () => {
  // _jwt is module state that survives between tests, so these assert that the
  // stored token is UNCHANGED rather than pinning a literal.
  it('refuses a non-OK response instead of minting undefined', async () => {
    configJwts = ['jwt-good', 429]
    await loadConfig(API, AGENT)
    const before = getJwt()

    await expect(loadConfig(API, AGENT)).rejects.toThrow('config request failed (429)')
    expect(getJwt()).toBe(before)
  })

  it('refuses a 200 whose body carries no jwt', async () => {
    configJwts = ['jwt-good', undefined]
    await loadConfig(API, AGENT)
    const before = getJwt()

    await expect(loadConfig(API, AGENT)).rejects.toThrow('no jwt')
    expect(getJwt()).toBe(before)
  })
})
