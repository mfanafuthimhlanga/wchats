'use client'
import { UserButton } from '@clerk/nextjs'
import { User } from 'lucide-react'

/**
 * UserAvatar
 *
 * Wraps the Clerk UserButton with a branded black-background box and a red
 * User icon overlay. The Clerk-fetched profile photo is hidden via
 * `userButtonAvatarImage { opacity: 0 }` set in the global ClerkProvider
 * appearance config (layout.tsx), so only the icon overlay is visible.
 *
 * The icon overlay sits at z-index 1 with pointer-events: none so all clicks
 * pass through to Clerk's trigger button underneath.
 */
export default function UserAvatar() {
  return (
    <div
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <UserButton />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          pointerEvents: 'none',
          zIndex: 1,
        }}
      >
        <User size={16} color="#ef4444" />
      </div>
    </div>
  )
}
