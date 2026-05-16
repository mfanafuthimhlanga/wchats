import { h, render } from 'preact'
import { Widget } from './Widget.jsx'
import './widget.css'
const params = new URLSearchParams(location.search)
const agentId = params.get('agent_id')
const apiBase = params.get('api') || ''
render(h(Widget, { agentId, apiBase }), document.getElementById('root') || document.body)
