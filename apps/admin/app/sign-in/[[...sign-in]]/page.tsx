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
        Sign in to W Chats
      </h1>
      {/* wordmark.svg already includes the logo mark */}
      <img src="/wordmark.svg" alt="w.chats" style={{ height: '24px' }} />
      <SignIn fallbackRedirectUrl="/agents" />
    </main>
  )
}
