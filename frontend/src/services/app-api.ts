const ACCESS_TOKEN_KEY = 'app_access_token';
const REFRESH_TOKEN_KEY = 'app_refresh_token';
const LOGIN_FLAG_KEY = 'isLoggedIn';
const ME_CACHE_TTL_MS = 30 * 1000;

type JsonValue = any;

interface Envelope<T> {
    code?: number | string;
    message?: string;
    data?: T;
    trace_id?: string;
}

export interface AppProfile {
    id: string;
    name?: string;
    avatar?: string;
    email?: string;
    phone?: string;
    health_goal?: string;
    current_state?: string;
    tastes?: string[] | string;
    taboos?: string[] | string;
    allergens?: string[] | string;
    joined_at?: string;
    joined_days?: number;
}

export interface AppPreferences {
    tastes?: string[] | string;
    taboos?: string[] | string;
    allergens?: string[] | string;
}

export interface AppHomeOverview {
    name?: string;
    health_goal?: string;
    current_state?: string;
    weather?: {
        city?: string;
        temperature_c?: number | null;
        status?: string;
        temperature_text?: string;
        display?: string;
        location?: {
            lat?: number;
            lng?: number;
        } | null;
    };
}

export interface AppChatSession {
    id?: string;
    session_id?: string;
    title?: string;
    created_at?: string;
}

export interface AppChatMessage {
    id?: string;
    role?: string;
    content?: string;
    created_at?: string;
}

export interface AppChatModelOption {
    value: string;
    provider: string;
    model: string;
    label: string;
    provider_label?: string;
}

export interface AppChatModelsResponse {
    models: AppChatModelOption[];
    default?: string;
    providers?: string[];
}

export type AppRestaurantSort = 'nearest' | 'rating_desc' | 'price_asc';

export interface AppRestaurant {
    id?: string;
    provider?: string;
    provider_id?: string;
    name?: string;
    rating?: number | null;
    distance_m?: number | null;
    distance_text?: string;
    price_text?: string;
    tag?: string;
    tags?: string[];
    lat?: number | null;
    lng?: number | null;
    navigation_url?: string | null;
    source?: string;
}

export interface AppRestaurantDetail extends AppRestaurant {
    raw?: unknown;
}

export interface AuthPayload {
    access_token?: string;
    refresh_token?: string;
    accessToken?: string;
    refreshToken?: string;
    token?: string;
}

export class ApiError extends Error {
    status?: number;
    code?: string | number;
    traceId?: string;

    constructor(message: string, options: { status?: number; code?: string | number; traceId?: string } = {}) {
        super(message);
        this.name = 'ApiError';
        this.status = options.status;
        this.code = options.code;
        this.traceId = options.traceId;
    }
}

function getApiBaseUrl(): string {
    const raw = (process.env.APP_API_BASE_URL || '').trim();
    if (!raw) return '';
    return raw.endsWith('/') ? raw.slice(0, -1) : raw;
}

function buildUrl(path: string): string {
    const normalized = path.startsWith('/') ? path : `/${path}`;
    return `${getApiBaseUrl()}/api/v1/app${normalized}`;
}

function getAccessToken(): string | null {
    return localStorage.getItem(ACCESS_TOKEN_KEY);
}

function getRefreshToken(): string | null {
    return localStorage.getItem(REFRESH_TOKEN_KEY);
}

let meCache: { data: AppProfile; expiresAt: number } | null = null;
let meInFlight: Promise<AppProfile> | null = null;

function clearMeCache(): void {
    meCache = null;
    meInFlight = null;
}

function setTokens(payload: AuthPayload | null | undefined): void {
    if (!payload) return;
    const accessToken = payload.access_token || payload.accessToken || payload.token;
    const refreshToken = payload.refresh_token || payload.refreshToken;

    if (accessToken) {
        if (localStorage.getItem(ACCESS_TOKEN_KEY) !== accessToken) {
            clearMeCache();
        }
        localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
        localStorage.setItem(LOGIN_FLAG_KEY, 'true');
    }
    if (refreshToken) {
        localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
    }
}

function clearTokens(): void {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    localStorage.removeItem(LOGIN_FLAG_KEY);
    clearMeCache();
}

function parseEnvelope<T>(raw: unknown, status?: number): T {
    if (!raw || typeof raw !== 'object') {
        if (raw === null || raw === undefined) {
            return undefined as T;
        }
        return raw as T;
    }

    const payload = raw as Envelope<T>;
    if (!Object.prototype.hasOwnProperty.call(payload, 'code')) {
        return raw as T;
    }

    const normalizedCode = Number(payload.code);
    const isSuccessCode = Number.isNaN(normalizedCode) || normalizedCode === 0 || normalizedCode === 200;

    if (!isSuccessCode) {
        throw new ApiError(payload.message || 'Request failed', {
            status,
            code: payload.code,
            traceId: payload.trace_id
        });
    }

    return payload.data as T;
}

async function parseResponseBody(response: Response): Promise<unknown> {
    const text = await response.text();
    if (!text) return undefined;
    try {
        return JSON.parse(text);
    } catch {
        return text;
    }
}

async function tryRefreshAccessToken(): Promise<boolean> {
    const refreshToken = getRefreshToken();
    if (!refreshToken) return false;

    const response = await fetch(buildUrl('/auth/refresh'), {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            refresh_token: refreshToken,
            refreshToken
        })
    });

    if (!response.ok) return false;

    const body = await parseResponseBody(response);
    const data = parseEnvelope<AuthPayload>(body, response.status);
    setTokens(data);
    return Boolean(getAccessToken());
}

interface RequestOptions {
    method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    body?: JsonValue;
    auth?: boolean;
    retryOnUnauthorized?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const {
        method = 'GET',
        body,
        auth = true,
        retryOnUnauthorized = true
    } = options;

    const headers: Record<string, string> = {
        'Content-Type': 'application/json'
    };

    if (auth) {
        const token = getAccessToken();
        if (token) {
            headers.Authorization = `Bearer ${token}`;
        }
    }

    const response = await fetch(buildUrl(path), {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined
    });

    if (response.status === 401 && auth && retryOnUnauthorized) {
        const refreshed = await tryRefreshAccessToken();
        if (refreshed) {
            return request<T>(path, { ...options, retryOnUnauthorized: false });
        }
        clearTokens();
    }

    const payload = await parseResponseBody(response);

    if (!response.ok) {
        let errorMessage = `HTTP ${response.status}`;
        try {
            const normalized = parseEnvelope<{ message?: string }>(payload, response.status);
            if (normalized && typeof normalized === 'object' && normalized.message) {
                errorMessage = normalized.message;
            }
        } catch (error) {
            if (error instanceof ApiError) {
                errorMessage = error.message;
                throw error;
            }
        }
        throw new ApiError(errorMessage, { status: response.status });
    }

    return parseEnvelope<T>(payload, response.status);
}

export const authStore = {
    isLoggedIn(): boolean {
        return Boolean(getAccessToken()) || localStorage.getItem(LOGIN_FLAG_KEY) === 'true';
    },
    getAccessToken(): string | null {
        return getAccessToken();
    },
    clear: clearTokens
};

export const appApi = {
    auth: {
        async register(payload: {
            name: string;
            password: string;
            phone?: string;
            email?: string;
        }) {
            return request('/auth/register', {
                method: 'POST',
                auth: false,
                body: payload
            });
        },

        async login(payload: {
            password: string;
            phone?: string;
            email?: string;
        }) {
            const data = await request<AuthPayload | { tokens?: AuthPayload }>('/auth/login', {
                method: 'POST',
                auth: false,
                body: payload
            });
            const tokenPayload = (data as { tokens?: AuthPayload }).tokens || (data as AuthPayload);
            setTokens(tokenPayload);
            return data;
        },

        async logout() {
            try {
                const refreshToken = getRefreshToken();
                await request('/auth/logout', {
                    method: 'POST',
                    auth: true,
                    body: {
                        refresh_token: refreshToken || undefined,
                        refreshToken: refreshToken || undefined
                    }
                });
            } finally {
                clearTokens();
            }
        },

        async changePassword(payload: {
            oldPassword: string;
            newPassword: string;
        }) {
            return request('/auth/password/change', {
                method: 'POST',
                auth: true,
                body: {
                    old_password: payload.oldPassword,
                    new_password: payload.newPassword,
                    oldPassword: payload.oldPassword,
                    newPassword: payload.newPassword
                }
            });
        }
    },

    me: {
        async get(options: { force?: boolean } = {}) {
            const { force = false } = options;
            const now = Date.now();

            if (!force && meCache && meCache.expiresAt > now) {
                return meCache.data;
            }

            if (!force && meInFlight) {
                return meInFlight;
            }

            meInFlight = request<AppProfile>('/me')
                .then((data) => {
                    meCache = {
                        data,
                        expiresAt: Date.now() + ME_CACHE_TTL_MS
                    };
                    return data;
                })
                .finally(() => {
                    meInFlight = null;
                });

            return meInFlight;
        },
        async update(payload: Partial<AppProfile>) {
            const updated = await request<AppProfile>('/me', {
                method: 'PATCH',
                body: payload
            });
            meCache = {
                data: updated,
                expiresAt: Date.now() + ME_CACHE_TTL_MS
            };
            return updated;
        },
        async updateGoalState(payload: { health_goal?: string; current_state?: string }) {
            const updated = await request<{ health_goal?: string; current_state?: string }>('/me/goal-state', {
                method: 'PATCH',
                body: payload
            });
            if (meCache) {
                meCache = {
                    data: {
                        ...meCache.data,
                        health_goal: updated.health_goal,
                        current_state: updated.current_state
                    },
                    expiresAt: Date.now() + ME_CACHE_TTL_MS
                };
            }
            return updated;
        },
        async getHomeOverview(location?: { lat: number; lng: number }) {
            if (!location) {
                return request<AppHomeOverview>('/home/overview');
            }
            const search = new URLSearchParams({
                lat: String(location.lat),
                lng: String(location.lng)
            });
            return request<AppHomeOverview>(`/home/overview?${search.toString()}`);
        }
    },

    preferences: {
        async get() {
            return request<AppPreferences>('/me/preferences');
        },
        async update(payload: AppPreferences) {
            return request<AppPreferences>('/me/preferences', {
                method: 'PATCH',
                body: payload
            });
        }
    },

    restaurants: {
        async list(params: {
            q?: string;
            sort?: AppRestaurantSort;
            tag?: string;
            lat?: number;
            lng?: number;
        } = {}) {
            const search = new URLSearchParams();
            if (params.q) search.set('q', params.q);
            if (params.sort) search.set('sort', params.sort);
            if (params.tag) search.set('tag', params.tag);
            if (typeof params.lat === 'number') search.set('lat', String(params.lat));
            if (typeof params.lng === 'number') search.set('lng', String(params.lng));
            const query = search.toString();
            return request<AppRestaurant[]>(`/restaurants${query ? `?${query}` : ''}`);
        },
        async detail(provider: string, providerId: string) {
            return request<AppRestaurantDetail>(`/restaurants/${provider}/${providerId}`);
        }
    },

    chat: {
        async listModels() {
            return request<AppChatModelsResponse>('/chat/models');
        },
        async createSession(payload: { title?: string } = {}) {
            return request<AppChatSession>('/chat/session', {
                method: 'POST',
                body: payload
            });
        },
        async listSessions() {
            const data = await request<{ sessions: AppChatSession[] }>('/chat/sessions');
            return data.sessions || [];
        },
        async getSessionMessages(sessionId: string) {
            return request<AppChatMessage[] | { messages?: AppChatMessage[] }>(`/chat/session/${sessionId}/messages`);
        },
        async renameSession(sessionId: string, title: string) {
            return request<{ updated: boolean; title?: string }>(`/chat/session/${sessionId}`, {
                method: 'PATCH',
                body: { title }
            });
        },
        async deleteSession(sessionId: string) {
            return request<{ deleted: boolean }>(`/chat/session/${sessionId}`, {
                method: 'DELETE'
            });
        }
    }
};
