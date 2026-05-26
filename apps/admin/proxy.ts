import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server'

// In demo mode all routes are public; otherwise only landing + auth pages
const isPublicRoute = createRouteMatcher(
  process.env.NEXT_PUBLIC_DEMO === 'true'
    ? ['(.*)']
    : ['/sign-in(.*)', '/sign-up(.*)', '/']
)

export default clerkMiddleware(async (auth, req) => {
  if (!isPublicRoute(req)) {
    await auth.protect()
  }
})

export const config = {
  matcher: [
    // Skip Next.js internals and static files
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    // Always run for API routes
    '/(api|trpc)(.*)',
    // Always run for Clerk frontend endpoints
    '/__clerk/(.*)',
  ],
}
