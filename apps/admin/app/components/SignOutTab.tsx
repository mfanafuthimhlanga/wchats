'use client'

import { useClerk } from '@clerk/nextjs'
import { LogOut } from 'lucide-react'
import { useState } from 'react'

export default function SignOutTab() {
  const { signOut } = useClerk()
  const [active, setActive] = useState(false)

  function handleSignOut() {
    if (active) return
    setActive(true)
    document.documentElement.classList.add('page-flip')
    setTimeout(() => {
      signOut({ redirectUrl: '/' })
    }, 550)
  }

  return (
    <button
      onClick={handleSignOut}
      aria-label="Sign out"
      className="sign-out-tab"
    >
      <LogOut
        size={18}
        strokeWidth={2.5}
        style={{ transform: 'rotate(180deg)', color: 'var(--red)' }}
      />
    </button>
  )
}
