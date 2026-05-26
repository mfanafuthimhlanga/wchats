import { SignIn } from '@clerk/nextjs'

export default function SignInPage() {
  return (
    <main
      style={{
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        background: 'transparent',
        gap: '24px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <img src="/logo-mark.svg" alt="W Chats" style={{ width: '30px', height: '30px' }} />
        <img src="/wordmark.svg" alt="w.chats" style={{ height: '20px' }} />
      </div>
      <SignIn fallbackRedirectUrl="/agents" />
    </main>
  )
}
