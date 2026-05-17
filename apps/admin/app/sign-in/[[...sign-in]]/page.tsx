import { SignIn } from '@clerk/nextjs'

export default function SignInPage() {
  return (
    <main style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
      <SignIn />
    </main>
  )
}
