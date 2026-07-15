import type { Metadata } from 'next'
import { ClerkProvider } from '@clerk/nextjs'
import QueryProvider from './components/QueryProvider'
import SignOutTab from './components/SignOutTab'
import './globals.css'

// Plain module-level constant — Server Component, no hooks, no CSS vars at runtime.
const clerkAppearance = {
  variables: {
    colorPrimary: '#F4748C',
    colorPrimaryForeground: '#0B0717',
    colorBackground: '#110C24',
    colorNeutral: '#C4BCD0',
    // Clerk v7 names + legacy aliases — set both so text is never dark-on-dark.
    colorForeground: '#F4EDE5',
    colorText: '#F4EDE5',
    colorMutedForeground: '#C4BCD0',
    colorTextSecondary: '#C4BCD0',
    colorInputBackground: 'rgba(11,7,23,0.42)',
    colorInputForeground: '#F4EDE5',
    colorInputText: '#F4EDE5',
    colorDanger: '#F87171',
    borderRadius: '14px',
    fontFamily: 'Inter, system-ui, sans-serif',
  },
  elements: {
    card: {
      background: 'rgba(17,12,31,0.80)',
      backdropFilter: 'blur(24px) saturate(115%)',
      border: '1px solid rgba(244,232,220,0.10)',
      boxShadow: '0 4px 12px rgba(24,14,46,0.35), 0 24px 48px rgba(11,7,23,0.6)',
      borderRadius: '20px',
    },
    headerTitle: {
      color: '#F4EDE5',
    },
    headerSubtitle: {
      color: '#C4BCD0',
    },
    socialButtonsBlockButton: {
      background: 'rgba(11,7,23,0.42)',
      border: '1px solid rgba(196,154,232,0.18)',
      color: '#F4EDE5',
    },
    socialButtonsBlockButtonText: {
      color: '#F4EDE5',
    },
    dividerLine: {
      background: 'rgba(196,154,232,0.18)',
    },
    dividerText: {
      color: '#8A82A0',
    },
    formFieldLabel: {
      color: '#C4BCD0',
    },
    footerActionText: {
      color: '#C4BCD0',
    },
    footerActionLink: {
      color: '#F4748C',
    },
    identityPreviewText: {
      color: '#F4EDE5',
    },
    formResendCodeLink: {
      color: '#F4748C',
    },
    otpCodeFieldInput: {
      color: '#F4EDE5',
      borderColor: 'rgba(196,154,232,0.18)',
    },
    formButtonPrimary: {
      background: '#F4748C',
      color: '#0B0717',
    },
    formFieldInput: {
      background: 'rgba(11,7,23,0.42)',
      border: '1px solid rgba(196,154,232,0.18)',
      color: '#F4EDE5',
      borderRadius: '8px',
    },
    footer: {
      background: 'transparent',
    },
    userButtonAvatarBox: {
      width: '32px',
      height: '32px',
      background: '#1E1638',
      borderRadius: '50%',
      overflow: 'hidden',
    },
    userButtonAvatarImage: {
      opacity: '0',
    },
    userButtonTrigger: {
      background: '#1E1638',
      borderRadius: '50%',
      padding: '4px',
    },
    userButtonPopoverCard: {
      background: '#1E1638',
      border: '1px solid rgba(196,154,232,0.18)',
    },
    userButtonPopoverActionButton: {
      color: '#C4BCD0',
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

// The Clerk instance is still named "VERIDIAN" upstream — override the visible
// copy here until the dashboard application name is renamed.
const clerkLocalization = {
  signIn: {
    start: {
      title: 'Sign in to W Chats',
      subtitle: 'Welcome back! Please sign in to continue',
    },
  },
  signUp: {
    start: {
      title: 'Create your W Chats account',
      subtitle: 'Welcome! Please fill in the details to get started',
    },
  },
}

export const metadata: Metadata = {
  title: 'W Chats',
  description: 'W Chats agent management',
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
      <body>
        <ClerkProvider appearance={clerkAppearance} localization={clerkLocalization}>
          <QueryProvider>
            {children}
            <SignOutTab />
          </QueryProvider>
        </ClerkProvider>
      </body>
    </html>
  )
}
