'use client'
import TopNav from '../components/TopNav'

export default function AgentsLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <TopNav />
      <main style={{ minHeight: 'calc(100vh - 60px)', background: 'transparent' }}>
        {children}
      </main>
    </>
  )
}
