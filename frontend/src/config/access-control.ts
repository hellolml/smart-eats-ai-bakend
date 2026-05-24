export const PUBLIC_ROUTES = ['/', '/blind-box', '/wheel', '/login', '/register', '/oauth/github/callback'] as const;

export const PROTECTED_ROUTES = [
    '/home-chef',
    '/food-hunter',
    '/ai-chat',
    '/travel-planner',
    '/skills',
    '/profile',
    '/preferences',
    '/security-settings',
    '/model-settings'
] as const;

export const GUEST_NAV_PATHS = ['/blind-box', '/wheel', '/login'] as const;

export const AUTH_NAV_PATHS = ['/', '/home-chef', '/food-hunter', '/travel-planner', '/skills', '/profile'] as const;

export function isProtectedRoute(pathname: string): boolean {
    return (PROTECTED_ROUTES as readonly string[]).includes(pathname);
}
