import type { Metadata } from 'next'
import { ClerkProvider } from '@clerk/nextjs'
import QueryProvider from './components/QueryProvider'
import SignOutTab from './components/SignOutTab'
import { GateProvider } from './components/gotham/GateProvider'
import './globals.css'

// Plain module-level constant — Server Component, no hooks, no CSS vars at runtime.
// Gotham "Bone on Graphite": colorPrimary is --live (bone, brightness not hue),
// colorBackground is --surface, colorDanger is --seal/--fail. No blur/glass —
// the console reads as one engraved graphite surface, not a translucent panel.
const clerkAppearance = {
  variables: {
    colorPrimary: '#E7E5E1',
    colorPrimaryForeground: '#0E1012',
    colorBackground: '#15181B',
    colorNeutral: '#9BA1A3',
    // Clerk v7 names + legacy aliases — set both so text is never dark-on-dark.
    colorForeground: '#E7E5E1',
    colorText: '#E7E5E1',
    colorMutedForeground: '#9BA1A3',
    colorTextSecondary: '#9BA1A3',
    colorInputBackground: '#08090B',
    colorInputForeground: '#E7E5E1',
    colorInputText: '#E7E5E1',
    colorDanger: '#E5484D',
    borderRadius: '6px',
    fontFamily: 'Inter, system-ui, sans-serif',
  },
  elements: {
    card: {
      background: '#15181B',
      border: '1px solid rgba(231,229,225,0.13)',
      boxShadow: '0 1px 2px rgba(0,0,0,0.3), 0 12px 32px rgba(0,0,0,0.4)',
      borderRadius: '6px',
    },
    headerTitle: {
      color: '#E7E5E1',
    },
    headerSubtitle: {
      color: '#9BA1A3',
    },
    socialButtonsBlockButton: {
      background: '#08090B',
      border: '1px solid rgba(231,229,225,0.13)',
      color: '#E7E5E1',
    },
    socialButtonsBlockButtonText: {
      color: '#E7E5E1',
    },
    dividerLine: {
      background: 'rgba(231,229,225,0.13)',
    },
    dividerText: {
      color: '#6B7275',
    },
    formFieldLabel: {
      color: '#9BA1A3',
    },
    footerActionText: {
      color: '#9BA1A3',
    },
    footerActionLink: {
      color: '#E7E5E1',
    },
    identityPreviewText: {
      color: '#E7E5E1',
    },
    formResendCodeLink: {
      color: '#E7E5E1',
    },
    otpCodeFieldInput: {
      color: '#E7E5E1',
      borderColor: 'rgba(231,229,225,0.13)',
    },
    formButtonPrimary: {
      background: '#E7E5E1',
      color: '#0E1012',
    },
    formFieldInput: {
      background: '#08090B',
      border: '1px solid rgba(231,229,225,0.13)',
      color: '#E7E5E1',
      borderRadius: '4px',
    },
    footer: {
      background: 'transparent',
    },
    userButtonAvatarBox: {
      width: '32px',
      height: '32px',
      background: '#1E2327',
      borderRadius: '50%',
      overflow: 'hidden',
    },
    userButtonAvatarImage: {
      opacity: '0',
    },
    userButtonTrigger: {
      background: '#1E2327',
      borderRadius: '50%',
      padding: '4px',
    },
    userButtonPopoverCard: {
      background: '#1E2327',
      border: '1px solid rgba(231,229,225,0.13)',
    },
    userButtonPopoverActionButton: {
      color: '#9BA1A3',
    },
    userButtonPopoverFooter: {
      borderTop: '1px solid rgba(231,229,225,0.06)',
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
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400;1,6..72,500&family=Space+Grotesk:wght@500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="tint">
        <ClerkProvider appearance={clerkAppearance} localization={clerkLocalization}>
          <QueryProvider>
            <GateProvider>
              {children}
              <SignOutTab />
            </GateProvider>
          </QueryProvider>
        </ClerkProvider>
      </body>
    </html>
  )
}
