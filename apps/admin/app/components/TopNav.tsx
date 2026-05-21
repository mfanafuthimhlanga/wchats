'use client'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import UserAvatar from './UserAvatar'

const NAV_LINKS = [
  { href: '/agents', label: 'Agents' },
  { href: '/evals', label: 'Evals' },
  { href: '/settings', label: 'Settings' },
]

export default function TopNav() {
  const pathname = usePathname()

  return (
    <nav
      style={{
        height: '56px',
        background: 'var(--bg)',
        borderBottom: '1px solid var(--border-soft)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 24px',
        position: 'sticky',
        top: 0,
        zIndex: 100,
      }}
    >
      {/* Logo */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginRight: '32px' }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/w-chats-lettermann.png"
          alt="Chats logo"
          style={{ width: '30px', height: '30px', objectFit: 'contain', animation: 'spin-cw 4s linear infinite' }}
        />
        <span style={{ fontFamily: 'var(--font-pixelify)', fontSize: '20px', color: 'var(--text-1)' }}>
          Chats
        </span>
      </div>

      {/* Nav links */}
      <div style={{ display: 'flex', gap: '4px', flex: 1 }}>
        {NAV_LINKS.map(({ href, label }) => {
          const active = pathname.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              style={{
                padding: '6px 14px',
                borderRadius: 'var(--radius-xs)',
                background: active ? 'var(--accent-dim)' : 'transparent',
                color: active ? 'var(--accent)' : 'var(--text-2)',
                fontWeight: active ? 600 : 400,
                fontSize: '14px',
                textDecoration: 'none',
              }}
            >
              {label}
            </Link>
          )
        })}
      </div>

      {/* User avatar */}
      <UserAvatar />
    </nav>
  )
}
