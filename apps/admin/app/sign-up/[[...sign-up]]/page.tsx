'use client'

/* =========================================================================
   Sign up — Gotham bare shell (UI-SPEC §5.1-A, §7 route inventory).

   Clerk's <SignUp> is unchanged; only the chrome around it is re-skinned.
   Its own visual theming flows from `clerkAppearance` in layout.tsx (20-03).

   OQ1 (three.js confinement) resolved: the specimen ships on landing only.
   Auth gets a lightweight static Gotham treatment — the graticule + bloom
   background layers PageChrome already paints, no three.js — keeping this
   route light. Mounting SceneMount here would be allowed by the design law
   (landing + auth is the permitted three.js boundary) but is intentionally
   skipped by default; the enforced invariant is that three.js appears on no
   route other than `/`, `/sign-in`, `/sign-up`, and here it appears on none.
   ========================================================================= */

import { SignUp } from '@clerk/nextjs'
import PageChrome, { type PageChromeOffsets } from '../../components/gotham/PageChrome'

const BARE_OFFSETS: PageChromeOffsets = {
  tl: { left: 22, top: 22 },
  tr: { right: 22, top: 22 },
  bl: { left: 22, bottom: 22 },
  br: { right: 22, bottom: 22 },
}

export default function SignUpPage() {
  return (
    <>
      <PageChrome offsets={BARE_OFFSETS} mobileOffsets={BARE_OFFSETS} skipTargetId="main" />

      <main
        id="main"
        style={{
          position: 'relative',
          zIndex: 1,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          alignItems: 'center',
          minHeight: '100vh',
          gap: '24px',
        }}
      >
        <h1
          style={{
            position: 'absolute',
            width: '1px',
            height: '1px',
            padding: 0,
            margin: '-1px',
            overflow: 'hidden',
            clip: 'rect(0, 0, 0, 0)',
            whiteSpace: 'nowrap',
            border: 0,
          }}
        >
          Create your W Chats account
        </h1>
        {/* wordmark.svg already includes the logo mark */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/wordmark.svg" alt="w.chats" style={{ height: '24px' }} />
        <SignUp fallbackRedirectUrl="/agents" />
      </main>
    </>
  )
}
