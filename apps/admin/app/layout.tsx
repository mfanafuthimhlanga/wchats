import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import { ClerkProvider } from '@clerk/nextjs'
import QueryProvider from './components/QueryProvider'
import SignOutTab from './components/SignOutTab'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })
const mono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono-loaded' })

// Plain module-level constant — Server Component, no hooks, no CSS vars at runtime.
const clerkAppearance = {
  variables: {
    colorPrimary: '#F4748C',
    colorBackground: '#140E2A',
    colorNeutral: '#C4B8D8',
    colorText: '#F0EBF8',
    colorTextSecondary: '#C4B8D8',
    colorInputBackground: '#1E1638',
    colorInputText: '#F0EBF8',
    colorDanger: '#F87171',
    borderRadius: '14px',
    fontFamily: 'Inter, system-ui, sans-serif',
  },
  elements: {
    card: {
      background: '#140E2A',
      border: '1px solid rgba(196,154,232,0.18)',
      boxShadow: '0 4px 12px rgba(0,0,0,0.35), 0 24px 48px rgba(11,7,23,0.6)',
      borderRadius: '20px',
    },
    formButtonPrimary: {
      background: '#F4748C',
      color: '#0B0717',
    },
    formFieldInput: {
      background: '#1E1638',
      border: '1px solid rgba(196,154,232,0.18)',
      color: '#F0EBF8',
      borderRadius: '8px',
    },
    userButtonAvatarBox: {
      width: '32px',
      height: '32px',
      background: '#1E1638',
      borderRadius: '8px',
      overflow: 'hidden',
    },
    userButtonAvatarImage: {
      opacity: '0',
    },
    userButtonTrigger: {
      background: '#1E1638',
      borderRadius: '8px',
      padding: '4px',
    },
    userButtonPopoverCard: {
      background: '#1E1638',
      border: '1px solid rgba(196,154,232,0.18)',
    },
    userButtonPopoverActionButton: {
      color: '#C4B8D8',
    },
    userButtonPopoverFooter: {
      borderTop: '1px solid rgba(196,154,232,0.10)',
    },
    avatarBox: {
      width: '32px',
      height: '32px',
    },
  },
}

export const metadata: Metadata = {
  title: 'Chats',
  description: 'Chats agent management',
  icons: {
    icon: [
      { url: '/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
      { url: '/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
    ],
    apple: { url: '/apple-touch-icon.png', sizes: '180x180' },
    other: [
      { rel: 'manifest', url: '/site.webmanifest' },
    ],
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT,WONK@9..144,300..800,0..100,0..1&family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className={`${inter.variable} ${mono.variable}`}>
        <ClerkProvider appearance={clerkAppearance}>
          <QueryProvider>
            {children}
            <SignOutTab />
          </QueryProvider>
        </ClerkProvider>
      </body>
    </html>
  )
}
