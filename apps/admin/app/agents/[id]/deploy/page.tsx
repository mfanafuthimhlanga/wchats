'use client'
import { useState, useEffect, use } from 'react'
import { useAuth } from '@clerk/nextjs'
import Link from 'next/link'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DeployTab = 'embed' | 'design'
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

interface WidgetConfig {
  appearance: 'floating-button' | 'floating-mini-modal' | 'slide-out-panel'
  launcher_shape: 'circle' | 'square'
  colors: {
    widget_bg: string
    header_bg: string
    header_text: string
    agent_bubble_bg: string
    agent_bubble_text: string
    user_bubble_bg: string
    user_bubble_text: string
    send_button: string
    input_bg: string
  }
  typography: {
    font_family: 'Inter' | 'System UI' | 'Georgia' | 'custom'
    font_custom_url: string | null
    border_radius_preset: 'sharp' | 'rounded' | 'pill'
  }
}

// ---------------------------------------------------------------------------
// Module-level constants
// ---------------------------------------------------------------------------

const DEFAULT_CONFIG: WidgetConfig = {
  appearance: 'floating-button',
  launcher_shape: 'circle',
  colors: {
    widget_bg: '#FDF9F5',
    header_bg: '#7B1C3A',
    header_text: '#FFFFFF',
    agent_bubble_bg: '#FDF9F5',
    agent_bubble_text: '#4A2030',
    user_bubble_bg: '#7B1C3A',
    user_bubble_text: '#FFFFFF',
    send_button: '#7B1C3A',
    input_bg: '#F7F0EA',
  },
  typography: {
    font_family: 'Inter',
    font_custom_url: null,
    border_radius_preset: 'rounded',
  },
}

const RADIUS_MAP: Record<'sharp' | 'rounded' | 'pill', string> = {
  sharp: '4px',
  rounded: '14px',
  pill: '24px',
}

const APPEARANCE_OPTIONS: { key: string; label: string; hint: string; icon: string }[] = [
  {
    key: 'floating-button',
    label: 'Floating Button',
    hint: 'Circular launcher fixed to page corner — chat opens on click',
    icon: '💬',
  },
  {
    key: 'floating-mini-modal',
    label: 'Floating Mini-modal',
    hint: 'Compact card showing greeting + input, expands on click',
    icon: '🪟',
  },
  {
    key: 'slide-out-panel',
    label: 'Slide-out Panel',
    hint: 'Full-height panel slides in from right page edge',
    icon: '▶',
  },
]

const COLOR_FIELDS: { key: keyof WidgetConfig['colors']; label: string }[] = [
  { key: 'widget_bg', label: 'Widget Background' },
  { key: 'header_bg', label: 'Header Background' },
  { key: 'header_text', label: 'Header Text' },
  { key: 'agent_bubble_bg', label: 'Agent Bubble Background' },
  { key: 'agent_bubble_text', label: 'Agent Bubble Text' },
  { key: 'user_bubble_bg', label: 'User Bubble Background' },
  { key: 'user_bubble_text', label: 'User Bubble Text' },
  { key: 'send_button', label: 'Send Button' },
  { key: 'input_bg', label: 'Input Field Background' },
]

function EMBED_SNIPPET(id: string): string {
  return '<script src="https://widget.veridian.app/widget.js" data-agent="' + id + '" async></script>'
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ColorRow({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '10px 0' }}>
      <input
        type="color"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: '24px', height: '24px', border: 'none', cursor: 'pointer', borderRadius: '50%', padding: 0 }}
        aria-label={`${label} color picker (currently ${value})`}
      />
      <span style={{ flex: 1, fontSize: '13px', color: 'var(--text-2)' }}>{label}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-3)' }}>{value}</span>
    </div>
  )
}

function AppearanceCard({
  option,
  selected,
  onSelect,
}: {
  option: { key: string; label: string; hint: string; icon: string }
  selected: boolean
  onSelect: () => void
}) {
  return (
    <label
      style={{
        display: 'block',
        padding: '16px',
        borderRadius: 'var(--radius-xs)',
        background: selected ? 'var(--accent-dim)' : 'var(--surface-2)',
        border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-soft)'}`,
        cursor: 'pointer',
        marginBottom: '8px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <div
          style={{
            width: '36px',
            height: '36px',
            background: 'var(--surface-3)',
            borderRadius: '8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '18px',
          }}
        >
          {option.icon}
        </div>
        <input
          type="radio"
          name="appearance"
          checked={selected}
          onChange={onSelect}
          style={{ accentColor: 'var(--accent)' }}
          aria-label={option.label}
        />
      </div>
      <div style={{ fontSize: '13.5px', fontWeight: 600, color: 'var(--text-1)', marginBottom: '4px' }}>
        {option.label}
      </div>
      <div style={{ fontSize: '12px', color: 'var(--text-3)', lineHeight: 1.45 }}>
        {option.hint}
      </div>
    </label>
  )
}

// ---------------------------------------------------------------------------
// DeployPage
// ---------------------------------------------------------------------------

export default function DeployPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  // State
  const [activeTab, setActiveTab] = useState<DeployTab>('embed')
  const [widgetConfig, setWidgetConfig] = useState<WidgetConfig>(DEFAULT_CONFIG)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied'>('idle')

  // ---------------------------------------------------------------------------
  // Load saved widget config on mount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const loadConfig = async () => {
      try {
        const token = await getToken()
        if (!token) {
          setLoadError('Not authenticated. Please sign in.')
          return
        }
        const r = await fetch(`${apiBase}/api/v1/agents/${id}/widget-config`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!r.ok) {
          setLoadError(`Failed to load widget config (HTTP ${r.status}).`)
          return
        }
        const data = await r.json()
        if (Object.keys(data).length > 0) {
          setWidgetConfig((prev) => ({
            ...prev,
            ...data,
            colors: { ...prev.colors, ...(data.colors ?? {}) },
            typography: { ...prev.typography, ...(data.typography ?? {}) },
          }))
        }
      } catch (err) {
        console.error(err)
        setLoadError('Failed to load widget config. Please refresh.')
      }
    }
    loadConfig()
  }, [id, apiBase]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleCopyEmbed = () => {
    navigator.clipboard.writeText(EMBED_SNIPPET(id))
    setCopyStatus('copied')
    setTimeout(() => setCopyStatus('idle'), 2000)
  }

  const handleSaveDesign = async () => {
    const url = widgetConfig.typography.font_custom_url
    if (url) {
      try {
        const parsed = new URL(url)
        if (parsed.protocol !== 'https:') {
          setSaveStatus('error')
          setLoadError('Custom font URL must use HTTPS.')
          return
        }
      } catch {
        setSaveStatus('error')
        setLoadError('Custom font URL is not a valid URL.')
        return
      }
    }
    setSaveStatus('saving')
    try {
      const token = await getToken()
      if (!token) {
        setSaveStatus('error')
        return
      }
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/widget-config`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(widgetConfig),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
    } catch (err) {
      console.error(err)
      setSaveStatus('error')
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div
      style={{
        padding: '32px 40px',
        maxWidth: '1400px',
        margin: '0 auto',
        fontFamily: 'var(--font-sans)',
      }}
    >
      {/* Back link */}
      <Link
        href={`/agents/${id}`}
        style={{
          fontSize: '14px',
          color: 'var(--accent)',
          textDecoration: 'none',
          display: 'inline-block',
          marginBottom: '24px',
        }}
      >
        ← Back to journey
      </Link>

      {/* Page header */}
      <h1 style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-1)', margin: '0 0 6px' }}>
        Deploy
      </h1>
      <p style={{ fontSize: '14px', color: 'var(--text-3)', margin: '0 0 24px' }}>
        Embed the agent on your site and customise its look.
      </p>

      {/* Load error alert */}
      {loadError && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            marginBottom: '20px',
            background: 'var(--red-bg)',
            border: '1px solid rgba(185,28,28,0.3)',
            borderRadius: 'var(--radius-xs)',
            fontSize: '14px',
            color: 'var(--red)',
          }}
        >
          {loadError}
        </div>
      )}

      {/* Sub-tab nav */}
      <div
        role="tablist"
        style={{
          display: 'flex',
          borderBottom: '1px solid var(--border-soft)',
          marginBottom: '24px',
          gap: '4px',
        }}
      >
        <button
          role="tab"
          aria-selected={activeTab === 'embed'}
          onClick={() => setActiveTab('embed')}
          style={{
            padding: '10px 20px',
            border: 'none',
            borderBottom: `2px solid ${activeTab === 'embed' ? 'var(--accent)' : 'transparent'}`,
            background: 'none',
            color: activeTab === 'embed' ? 'var(--accent)' : 'var(--text-3)',
            fontWeight: activeTab === 'embed' ? 600 : 400,
            fontSize: '14px',
            cursor: activeTab === 'embed' ? 'default' : 'pointer',
            fontFamily: 'var(--font-sans)',
          }}
        >
          Embed Code
        </button>
        <button
          role="tab"
          aria-selected={activeTab === 'design'}
          onClick={() => setActiveTab('design')}
          style={{
            padding: '10px 20px',
            border: 'none',
            borderBottom: `2px solid ${activeTab === 'design' ? 'var(--accent)' : 'transparent'}`,
            background: 'none',
            color: activeTab === 'design' ? 'var(--accent)' : 'var(--text-3)',
            fontWeight: activeTab === 'design' ? 600 : 400,
            fontSize: '14px',
            cursor: activeTab === 'design' ? 'default' : 'pointer',
            fontFamily: 'var(--font-sans)',
          }}
        >
          Customise Widget
        </button>
      </div>

      {/* Tab content */}
      {activeTab === 'embed' && (
        <div
          style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border-soft)',
            borderRadius: 'var(--radius-xs)',
            padding: '24px',
          }}
        >
          <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-1)', margin: '0 0 8px' }}>
            Paste this snippet into your site&apos;s &lt;head&gt;
          </h2>
          <p style={{ fontSize: '13px', color: 'var(--text-3)', margin: '0 0 0' }}>
            The widget loads asynchronously and is under 20kb gzipped.
          </p>
          <pre
            style={{
              background: 'var(--accent-deep)',
              color: 'rgba(255,255,255,0.92)',
              padding: '16px',
              borderRadius: 'var(--radius-xs)',
              fontFamily: 'var(--font-mono)',
              fontSize: '13px',
              overflowX: 'auto',
              margin: '12px 0 16px',
            }}
          >
            <code>{EMBED_SNIPPET(id)}</code>
          </pre>
          <button
            onClick={handleCopyEmbed}
            style={{
              padding: '10px 18px',
              background: 'var(--accent)',
              color: '#fff',
              border: 'none',
              borderRadius: 'var(--radius-xs)',
              cursor: 'pointer',
              fontWeight: 600,
              fontSize: '14px',
              fontFamily: 'var(--font-sans)',
            }}
          >
            {copyStatus === 'copied' ? 'Copied!' : 'Copy snippet'}
          </button>
        </div>
      )}

      {activeTab === 'design' && (
        <div style={{ padding: '0' }}>
          {/* 3-column design panel */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '255px 310px 1fr',
              gap: '24px',
            }}
          >
            {/* Column 1: Appearance Mode */}
            <div>
              <p
                style={{
                  fontSize: '11px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--text-3)',
                  marginBottom: '12px',
                  paddingBottom: '12px',
                  borderBottom: '1px solid var(--border-soft)',
                  margin: '0 0 12px',
                }}
              >
                APPEARANCE MODE
              </p>
              {APPEARANCE_OPTIONS.map((opt) => (
                <AppearanceCard
                  key={opt.key}
                  option={opt}
                  selected={widgetConfig.appearance === opt.key}
                  onSelect={() =>
                    setWidgetConfig((c) => ({ ...c, appearance: opt.key as WidgetConfig['appearance'] }))
                  }
                />
              ))}
            </div>

            {/* Column 2: Style Pickers */}
            <div
              style={{
                background: 'var(--surface-2)',
                border: '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                padding: '16px',
              }}
            >
              {/* Colors sub-section */}
              <p
                style={{
                  fontSize: '11px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--text-3)',
                  marginBottom: '8px',
                  paddingBottom: '8px',
                  borderBottom: '1px solid var(--border-soft)',
                  margin: '0 0 8px',
                }}
              >
                COLORS
              </p>
              {COLOR_FIELDS.map((f) => (
                <ColorRow
                  key={f.key}
                  label={f.label}
                  value={widgetConfig.colors[f.key]}
                  onChange={(v) =>
                    setWidgetConfig((c) => ({ ...c, colors: { ...c.colors, [f.key]: v } }))
                  }
                />
              ))}

              {/* Typography sub-section */}
              <div style={{ marginTop: '20px' }}>
                <p
                  style={{
                    fontSize: '11px',
                    textTransform: 'uppercase',
                    letterSpacing: '0.08em',
                    color: 'var(--text-3)',
                    marginBottom: '8px',
                    paddingBottom: '8px',
                    borderBottom: '1px solid var(--border-soft)',
                    margin: '0 0 8px',
                  }}
                >
                  TYPOGRAPHY
                </p>
                {/* Font family select */}
                <div style={{ marginBottom: '12px' }}>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '12px',
                      color: 'var(--text-3)',
                      marginBottom: '4px',
                    }}
                  >
                    Font Family
                  </label>
                  <select
                    value={widgetConfig.typography.font_family}
                    onChange={(e) =>
                      setWidgetConfig((c) => ({
                        ...c,
                        typography: {
                          ...c.typography,
                          font_family: e.target.value as WidgetConfig['typography']['font_family'],
                        },
                      }))
                    }
                    style={{
                      width: '100%',
                      background: 'var(--surface-1)',
                      border: '1px solid var(--border)',
                      padding: '8px 10px',
                      borderRadius: 'var(--radius-xs)',
                      fontSize: '13px',
                      color: 'var(--text-1)',
                      fontFamily: 'var(--font-sans)',
                    }}
                  >
                    <option value="Inter">Inter</option>
                    <option value="System UI">System UI</option>
                    <option value="Georgia">Georgia</option>
                    <option value="custom">Custom URL</option>
                  </select>
                </div>

                {/* Custom font URL input — shown only when custom is selected */}
                {widgetConfig.typography.font_family === 'custom' && (
                  <div style={{ marginBottom: '12px' }}>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '12px',
                        color: 'var(--text-3)',
                        marginBottom: '4px',
                      }}
                    >
                      Custom Font URL
                    </label>
                    <input
                      type="url"
                      placeholder="https://..."
                      value={widgetConfig.typography.font_custom_url ?? ''}
                      onChange={(e) =>
                        setWidgetConfig((c) => ({
                          ...c,
                          typography: { ...c.typography, font_custom_url: e.target.value || null },
                        }))
                      }
                      style={{
                        width: '100%',
                        background: 'var(--surface-1)',
                        border: '1px solid var(--border)',
                        padding: '8px 10px',
                        borderRadius: 'var(--radius-xs)',
                        fontSize: '13px',
                        color: 'var(--text-1)',
                        fontFamily: 'var(--font-sans)',
                        boxSizing: 'border-box',
                      }}
                    />
                  </div>
                )}

                {/* Border radius preset */}
                <div>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '12px',
                      color: 'var(--text-3)',
                      marginBottom: '6px',
                    }}
                  >
                    Border Radius
                  </label>
                  <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                    {(['sharp', 'rounded', 'pill'] as const).map((preset) => {
                      const isSelected = widgetConfig.typography.border_radius_preset === preset
                      return (
                        <button
                          key={preset}
                          onClick={() =>
                            setWidgetConfig((c) => ({
                              ...c,
                              typography: { ...c.typography, border_radius_preset: preset },
                            }))
                          }
                          style={{
                            flex: 1,
                            padding: '6px 0',
                            background: isSelected ? 'var(--accent)' : 'var(--surface-2)',
                            color: isSelected ? '#fff' : 'var(--text-3)',
                            border: isSelected ? 'none' : '1px solid var(--border)',
                            borderRadius: 'var(--radius-xs)',
                            cursor: 'pointer',
                            fontSize: '12px',
                            fontWeight: isSelected ? 600 : 400,
                            fontFamily: 'var(--font-sans)',
                            textTransform: 'capitalize',
                          }}
                        >
                          {preset.charAt(0).toUpperCase() + preset.slice(1)}
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>
            </div>

            {/* Column 3: Live Preview */}
            <div
              style={{
                background: 'var(--surface-2)',
                border: '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                padding: '24px',
                minHeight: '400px',
              }}
            >
              <p
                style={{
                  fontSize: '11px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--text-3)',
                  marginBottom: '16px',
                  paddingBottom: '12px',
                  borderBottom: '1px solid var(--border-soft)',
                  margin: '0 0 16px',
                }}
              >
                LIVE PREVIEW
              </p>

              {/* Widget mock — decorative, no API calls */}
              <div aria-hidden="true">
                {/* Chat window */}
                <div
                  style={{
                    width: '320px',
                    minHeight: '380px',
                    background: widgetConfig.colors.widget_bg,
                    borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                    border: '1px solid rgba(0,0,0,0.06)',
                    boxShadow: '0 4px 8px rgba(74,32,48,0.04), 0 16px 32px rgba(74,32,48,0.08)',
                    overflow: 'hidden',
                    fontFamily:
                      widgetConfig.typography.font_family === 'custom'
                        ? 'sans-serif'
                        : widgetConfig.typography.font_family,
                  }}
                >
                  {/* Header */}
                  <div
                    style={{
                      padding: '12px 16px',
                      background: widgetConfig.colors.header_bg,
                      color: widgetConfig.colors.header_text,
                      fontWeight: 600,
                      fontSize: '14px',
                    }}
                  >
                    Veridian assistant
                  </div>

                  {/* Chat body */}
                  <div
                    style={{
                      padding: '16px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '10px',
                      height: '240px',
                      overflowY: 'auto',
                    }}
                  >
                    {/* Agent bubble */}
                    <div
                      style={{
                        alignSelf: 'flex-start',
                        maxWidth: '80%',
                        padding: '10px 14px',
                        background: widgetConfig.colors.agent_bubble_bg,
                        color: widgetConfig.colors.agent_bubble_text,
                        borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                        fontSize: '13px',
                      }}
                    >
                      Hi! Ask me anything about the business.
                    </div>

                    {/* User bubble */}
                    <div
                      style={{
                        alignSelf: 'flex-end',
                        maxWidth: '80%',
                        padding: '10px 14px',
                        background: widgetConfig.colors.user_bubble_bg,
                        color: widgetConfig.colors.user_bubble_text,
                        borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                        fontSize: '13px',
                      }}
                    >
                      What are your business hours?
                    </div>
                  </div>

                  {/* Input row */}
                  <div
                    style={{
                      display: 'flex',
                      gap: '8px',
                      padding: '12px 16px',
                      borderTop: '1px solid rgba(0,0,0,0.06)',
                    }}
                  >
                    <input
                      aria-hidden="true"
                      tabIndex={-1}
                      placeholder="Type a message…"
                      readOnly
                      style={{
                        flex: 1,
                        padding: '8px 12px',
                        background: widgetConfig.colors.input_bg,
                        border: '1px solid rgba(0,0,0,0.08)',
                        borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                        fontSize: '13px',
                        color: 'var(--text-3)',
                        fontFamily: 'inherit',
                      }}
                    />
                    <button
                      tabIndex={-1}
                      style={{
                        padding: '8px 14px',
                        background: widgetConfig.colors.send_button,
                        color: '#fff',
                        border: 'none',
                        borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                        fontWeight: 600,
                        fontSize: '13px',
                        cursor: 'default',
                        fontFamily: 'inherit',
                      }}
                    >
                      Send
                    </button>
                  </div>
                </div>

                {/* Floating launcher mock — shown when appearance is floating-button */}
                {widgetConfig.appearance === 'floating-button' && (
                  <div
                    style={{
                      marginTop: '12px',
                      width: '42px',
                      height: '42px',
                      borderRadius: '50%',
                      background: widgetConfig.colors.send_button,
                      color: '#fff',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '20px',
                      marginLeft: 'auto',
                    }}
                  >
                    💬
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Save Design row */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              alignItems: 'center',
              gap: '12px',
              marginTop: '24px',
              paddingTop: '16px',
              borderTop: '1px solid var(--border-soft)',
            }}
          >
            {saveStatus === 'saved' && (
              <span style={{ color: 'var(--green)', fontSize: '13px', fontWeight: 600 }}>
                ✓ Saved
              </span>
            )}
            {saveStatus === 'error' && (
              <span style={{ color: 'var(--red)', fontSize: '13px' }}>
                Save failed — retry
              </span>
            )}
            <button
              onClick={handleSaveDesign}
              disabled={saveStatus === 'saving'}
              style={{
                padding: '12px 24px',
                background: 'var(--accent)',
                color: '#fff',
                border: 'none',
                borderRadius: 'var(--radius-xs)',
                fontWeight: 600,
                fontSize: '14px',
                cursor: saveStatus === 'saving' ? 'wait' : 'pointer',
                fontFamily: 'var(--font-sans)',
              }}
            >
              {saveStatus === 'saving' ? 'Saving…' : 'Save Design'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
