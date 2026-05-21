import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import localFont from 'next/font/local'
import { ClerkProvider } from '@clerk/nextjs'
import QueryProvider from './components/QueryProvider'
import SignOutTab from './components/SignOutTab'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })
const mono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono-loaded' })
const fungkyBrow = localFont({ src: '../public/fonts/FungkyBrowDEMO.otf', variable: '--font-pixelify' })

// Plain module-level constant — Server Component, no hooks, no CSS vars at runtime.
const clerkAppearance = {
  variables: {
    colorPrimary: '#7B1C3A',
    colorBackground: '#FFFCF9',
    colorNeutral: '#4A2030',
    colorText: '#1A0A0F',
    colorTextSecondary: '#4A2030',
    colorInputBackground: '#F7F0EA',
    colorInputText: '#1A0A0F',
    colorDanger: '#B91C1C',
    borderRadius: '14px',
    fontFamily: 'Inter, system-ui, sans-serif',
  },
  elements: {
    card: {
      background: '#FFFCF9',
      border: '1px solid #D9CCBE',
      boxShadow: '0 1px 2px rgba(74,32,48,0.04), 0 4px 12px rgba(74,32,48,0.06)',
      borderRadius: '20px',
    },
    formButtonPrimary: {
      background: '#7B1C3A',
      color: '#ffffff',
    },
    formFieldInput: {
      background: '#F7F0EA',
      border: '1px solid #D9CCBE',
      color: '#1A0A0F',
      borderRadius: '8px',
    },
    userButtonAvatarBox: {
      width: '32px',
      height: '32px',
      background: '#1A0A0F',
      borderRadius: '8px',
      overflow: 'hidden',
    },
    userButtonAvatarImage: {
      opacity: '0',
    },
    userButtonTrigger: {
      background: '#1A0A0F',
      borderRadius: '8px',
      padding: '4px',
    },
    userButtonPopoverCard: {
      background: '#FFFCF9',
      border: '1px solid #D9CCBE',
      boxShadow: '0 4px 16px rgba(26,10,15,0.12)',
    },
    userButtonPopoverActionButton: {
      color: '#4A2030',
    },
    userButtonPopoverFooter: {
      borderTop: '1px solid #EDE3D8',
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
      <body className={`${inter.variable} ${mono.variable} ${fungkyBrow.variable}`}>
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
