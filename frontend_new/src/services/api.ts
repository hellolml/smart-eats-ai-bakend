const ACCESS_TOKEN_KEY = 'app_access_token';
const REFRESH_TOKEN_KEY = 'app_refresh_token';
const CSRF_TOKEN_KEY = 'app_csrf_token';
const LOGIN_FLAG_KEY = 'isLoggedIn';

let refreshInFlight: Promise<boolean> | null = null;

type JsonValue = any;

interface Envelope<T> {
  code?: number | string;
  message?: string;
  data?: T;
  trace_id?: string;
}

export interface AuthPayload {
  access_token?: string;
  refresh_token?: string;
  csrf_token?: string;
  accessToken?: string;
  refreshToken?: string;
  csrfToken?: string;
  token?: string;
}

export interface ChatSession {
  id?: string;
  session_id?: string;
  title?: string;
  scene?: string;
  created_at?: string;
}

export interface ChatAttachment {
  attachment_id: string;
  kind: 'image';
  object_key: string;
  filename: string;
  content_type: string;
  size_bytes: number;
}

export interface ChatStreamResult {
  text: string;
  qrCodeUrl?: string;
  schemaUrl?: string;
  finalJson?: Record<string, unknown>;
}

export interface ChatSessionSummary {
  session_id: string;
  scene: string;
  title: string;
  created_at: string;
}

export interface ChatSessionListResult {
  sessions: ChatSessionSummary[];
  offset: number;
  limit: number;
}

export interface ChatMessageRecord {
  id: string;
  role: 'user' | 'assistant' | 'tool' | string;
  content: string;
  tool_name?: string | null;
  tool_payload?: Record<string, unknown> | null;
  created_at?: string;
}

export interface ChatMessageListResult {
  messages: ChatMessageRecord[];
  offset: number;
  limit: number;
}

export interface ChatLocationContext {
  lat: number;
  lng: number;
  accuracy?: number;
  city?: string;
  source?: string;
  updatedAt?: number;
}

export interface DecisionResult {
  decision: {
    type: 'restaurant' | 'recipe' | 'fallback';
    title: string;
    confidence?: number;
    navigation_url?: string | null;
  };
  reasons: string[];
  actions: Array<{ type: string; label: string; url: string }>;
  meta?: Record<string, unknown>;
}

export interface QuickFilterState {
  flow_id: string;
  round: number;
  answers: Record<string, string>;
  done: boolean;
  next_question?: {
    slot: string;
    question: string;
    options: string[];
  } | null;
  result?: DecisionResult;
}

export interface PlanRecord {
  id: string;
  user_id?: string;
  session_id?: string;
  title: string;
  plan_type: string;
  status: string;
  date_text?: string | null;
  source_text: string;
  qr_code_url?: string | null;
  schema_url?: string | null;
  plan_json: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export class ApiError extends Error {
  status?: number;
  code?: string | number;

  constructor(message: string, options: { status?: number; code?: string | number } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
  }
}

function getApiBaseUrl(): string {
  const raw = (process.env.APP_API_BASE_URL || '').trim();
  return raw.endsWith('/') ? raw.slice(0, -1) : raw;
}

function buildQuery(params?: Record<string, string | number | boolean | null | undefined>): string {
  if (!params) return '';
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    query.set(key, String(value));
  });
  const text = query.toString();
  return text ? `?${text}` : '';
}

function buildUrl(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  const base = getApiBaseUrl();
  if (!base) return `/api/v1/app${normalized}`;
  if (base.endsWith('/api/v1/app')) return `${base}${normalized}`;
  if (base.endsWith('/api/v1')) return `${base}/app${normalized}`;
  return `${base}/api/v1/app${normalized}`;
}

function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

function getCsrfToken(): string | null {
  return localStorage.getItem(CSRF_TOKEN_KEY);
}

function setTokens(payload: AuthPayload | { tokens?: AuthPayload } | null | undefined): void {
  if (!payload) return;
  const source = ((payload as { tokens?: AuthPayload }).tokens || payload) as AuthPayload;
  const accessToken = source.access_token || source.accessToken || source.token;
  const refreshToken = source.refresh_token || source.refreshToken;
  const csrfToken = source.csrf_token || source.csrfToken;

  if (accessToken) {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    localStorage.setItem(LOGIN_FLAG_KEY, 'true');
  }
  if (refreshToken) localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  if (csrfToken) localStorage.setItem(CSRF_TOKEN_KEY, csrfToken);
}

function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(CSRF_TOKEN_KEY);
  localStorage.removeItem(LOGIN_FLAG_KEY);
}

function toZhErrorMessage(message: string): string {
  const text = (message || '').trim();
  if (!text) return '请求失败，请稍后重试';
  if (/request timed out|timed out/i.test(text)) return '模型响应超时，请稍后重试；如果行程较复杂，可以先减少一次输入的信息量';
  if (/coding_plan_subscription_expired|subscription is expired/i.test(text)) return '当前模型订阅已过期，请在模型管理中切换到可用模型后重试';
  if (/invalid token|missing bearer token|invalid token type/i.test(text)) return '登录状态已失效，请重新登录';
  if (/invalid credentials/i.test(text)) return '账号或密码错误';
  if (/email or phone already exists/i.test(text)) return '手机号或邮箱已注册';
  if (/account must be a valid phone or email/i.test(text)) return '账号格式不正确，请输入手机号或邮箱';
  if (/phone must match/i.test(text)) return '手机号格式不正确';
  if (/password length must be between 8 and 64/i.test(text)) return '密码长度需在 8 到 64 位之间';
  if (/password must contain at least one letter/i.test(text)) return '密码需至少包含一个字母';
  if (/password must contain at least one number/i.test(text)) return '密码需至少包含一个数字';
  if (/refresh token|csrf token/i.test(text)) return '登录状态已失效，请重新登录';
  return text;
}

function parseEnvelope<T>(raw: unknown, status?: number): T {
  if (!raw || typeof raw !== 'object') return raw as T;
  const payload = raw as Envelope<T>;
  if (!Object.prototype.hasOwnProperty.call(payload, 'code')) return raw as T;
  const normalizedCode = Number(payload.code);
  const ok = Number.isNaN(normalizedCode) || normalizedCode === 0 || normalizedCode === 200;
  if (!ok) {
    throw new ApiError(toZhErrorMessage(payload.message || 'Request failed'), {
      status,
      code: payload.code
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

function isUnauthorizedPayload(payload: unknown, status?: number): boolean {
  if (status === 401) return true;
  if (payload && typeof payload === 'object') {
    const code = Number((payload as Envelope<unknown>).code);
    const message = String((payload as Envelope<unknown>).message || '');
    return code === 40101 || /invalid token|missing bearer token|refresh token/i.test(message);
  }
  return false;
}

async function tryRefreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const refreshToken = getRefreshToken();
    const csrfToken = getCsrfToken();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json'
    };
    if (csrfToken) headers['x-csrf-token'] = csrfToken;

    const response = await fetch(buildUrl('/auth/refresh'), {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify(refreshToken ? { refresh_token: refreshToken, refreshToken } : {})
    });
    if (!response.ok) return false;
    const payload = await parseResponseBody(response);
    const data = parseEnvelope<AuthPayload | { tokens?: AuthPayload }>(payload, response.status);
    setTokens(data);
    return Boolean(getAccessToken());
  })().catch(() => false).finally(() => {
    refreshInFlight = null;
  });

  return refreshInFlight;
}

async function request<T>(path: string, options: { method?: string; body?: JsonValue; auth?: boolean; retryOnUnauthorized?: boolean } = {}): Promise<T> {
  const { method = 'GET', body, auth = true, retryOnUnauthorized = true } = options;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json'
  };
  if (auth) {
    const token = getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  if (method !== 'GET') {
    const csrf = getCsrfToken();
    if (csrf) headers['x-csrf-token'] = csrf;
  }

  const response = await fetch(buildUrl(path), {
    method,
    headers,
    credentials: 'include',
    body: body ? JSON.stringify(body) : undefined
  });
  const payload = await parseResponseBody(response);
  if (auth && retryOnUnauthorized && isUnauthorizedPayload(payload, response.status)) {
    const refreshed = await tryRefreshAccessToken();
    if (refreshed) return request<T>(path, { ...options, retryOnUnauthorized: false });
    clearTokens();
  }
  if (!response.ok) {
    try {
      parseEnvelope(payload, response.status);
    } catch (error) {
      if (error instanceof ApiError) throw error;
    }
    throw new ApiError(`HTTP ${response.status}`, { status: response.status });
  }
  return parseEnvelope<T>(payload, response.status);
}

async function requestForm<T>(path: string, formData: FormData, options: { retryOnUnauthorized?: boolean } = {}): Promise<T> {
  const { retryOnUnauthorized = true } = options;
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const csrf = getCsrfToken();
  if (csrf) headers['x-csrf-token'] = csrf;

  const response = await fetch(buildUrl(path), {
    method: 'POST',
    headers,
    credentials: 'include',
    body: formData
  });
  const payload = await parseResponseBody(response);
  if (retryOnUnauthorized && isUnauthorizedPayload(payload, response.status)) {
    const refreshed = await tryRefreshAccessToken();
    if (refreshed) return requestForm<T>(path, formData, { retryOnUnauthorized: false });
    clearTokens();
  }
  if (!response.ok) {
    try {
      parseEnvelope(payload, response.status);
    } catch (error) {
      if (error instanceof ApiError) throw error;
    }
    throw new ApiError(`HTTP ${response.status}`, { status: response.status });
  }
  return parseEnvelope<T>(payload, response.status);
}

function getSessionId(session: ChatSession): string {
  return session.id || session.session_id || '';
}

function extractSseText(value: unknown): string {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(extractSseText).filter(Boolean).join('');
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    for (const key of ['token', 'content', 'text', 'delta', 'message', 'answer', 'response', 'result', 'final', 'output']) {
      const text = extractSseText(record[key]);
      if (text) return text;
    }
  }
  return '';
}

function findStringByKeys(value: unknown, keys: string[]): string | undefined {
  if (!value || typeof value !== 'object') return undefined;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findStringByKeys(item, keys);
      if (found) return found;
    }
    return undefined;
  }
  const record = value as Record<string, unknown>;
  for (const key of keys) {
    const direct = record[key];
    if (typeof direct === 'string' && direct.trim()) return direct.trim();
  }
  for (const child of Object.values(record)) {
    const found = findStringByKeys(child, keys);
    if (found) return found;
  }
  return undefined;
}

function extractErrorMessage(value: unknown): string | undefined {
  if (!value) return undefined;
  if (typeof value === 'string') return value.trim() || undefined;
  return findStringByKeys(value, ['message', 'detail', 'error', 'msg']);
}

function extractFinalText(value: unknown): string {
  const direct = extractSseText(value);
  if (direct) return direct;
  if (!value || typeof value !== 'object') return '';
  const record = value as Record<string, unknown>;
  const answer = record.answer && typeof record.answer === 'object' ? record.answer as Record<string, unknown> : record;
  const recommendations = Array.isArray(answer.recommendations) ? answer.recommendations : [];
  if (recommendations.length) {
    return recommendations.map((item) => {
      if (!item || typeof item !== 'object') return '';
      const row = item as Record<string, unknown>;
      const title = String(row.title || row.name || '').trim();
      const reason = String(row.reason || row.description || '').trim();
      return [title, reason].filter(Boolean).join('\n');
    }).filter(Boolean).join('\n\n');
  }
  const decision = answer.decision && typeof answer.decision === 'object' ? answer.decision as Record<string, unknown> : undefined;
  if (decision) {
    const title = String(decision.title || '').trim();
    const reasons = Array.isArray(answer.reasons) ? answer.reasons.map(String).join('\n') : '';
    return [title, reasons].filter(Boolean).join('\n');
  }
  return '';
}

export const authStore = {
  isLoggedIn(): boolean {
    return Boolean(getAccessToken());
  },
  token(): string | null {
    return getAccessToken();
  },
  isAuthError(error: unknown): boolean {
    if (!(error instanceof ApiError)) return false;
    return error.status === 401 || Number(error.code) === 40101 || /登录状态已失效|invalid token/i.test(error.message);
  },
  clear: clearTokens
};

export const appApi = {
  auth: {
    async login(payload: { account: string; password: string }) {
      const data = await request<AuthPayload | { tokens?: AuthPayload }>('/auth/login', {
        method: 'POST',
        auth: false,
        body: payload
      });
      setTokens(data);
      return data;
    },
    async register(payload: { name: string; account: string; password: string }) {
      const isEmail = payload.account.includes('@');
      const data = await request<AuthPayload | { tokens?: AuthPayload }>('/auth/register', {
        method: 'POST',
        auth: false,
        body: {
          name: payload.name,
          password: payload.password,
          ...(isEmail ? { email: payload.account } : { phone: payload.account })
        }
      });
      setTokens(data);
      return data;
    },
    async logout() {
      clearTokens();
    }
  },
  chat: {
    async createSession(payload: { title?: string; scene?: string }) {
      return request<ChatSession>('/chat/session', {
        method: 'POST',
        body: payload
      });
    },
    async uploadAttachment(sessionId: string, file: File) {
      const formData = new FormData();
      formData.append('file', file);
      return requestForm<ChatAttachment>(`/chat/session/${sessionId}/attachments`, formData);
    },
    async stream(sessionId: string, message: string, options: {
      scene?: string;
      attachments?: ChatAttachment[];
      travelAction?: string;
      travelPayload?: Record<string, unknown>;
      clientContextOverrides?: Record<string, unknown>;
      onDelta?: (text: string) => void;
      onVisionError?: (message: string) => void;
      retryOnUnauthorized?: boolean;
      signal?: AbortSignal;
    } = {}): Promise<ChatStreamResult> {
      const token = getAccessToken();
      const headers: Record<string, string> = {
        Accept: 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Content-Type': 'application/json'
      };
      if (token) headers.Authorization = `Bearer ${token}`;
      const csrf = getCsrfToken();
      if (csrf) headers['x-csrf-token'] = csrf;

      const response = await fetch(buildUrl(`/chat/session/${sessionId}/stream`), {
        method: 'POST',
        headers,
        credentials: 'include',
        signal: options.signal,
        body: JSON.stringify({
          message,
          scene: options.scene,
          attachments: options.attachments,
          travel_action: options.travelAction,
          travel_payload: options.travelPayload,
          client_context_overrides: options.clientContextOverrides
        })
      });
      if (!response.ok) {
        const payload = await parseResponseBody(response);
        if ((options.retryOnUnauthorized ?? true) && isUnauthorizedPayload(payload, response.status)) {
          const refreshed = await tryRefreshAccessToken();
          if (refreshed) return appApi.chat.stream(sessionId, message, { ...options, retryOnUnauthorized: false });
          clearTokens();
        }
        const envelope = payload && typeof payload === 'object' ? payload as Envelope<unknown> : undefined;
        throw new ApiError(toZhErrorMessage(extractErrorMessage(payload) || `HTTP ${response.status}`), { status: response.status, code: envelope?.code });
      }
      if (!response.body) return { text: '' };

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let collected = '';
      let qrCodeUrl = '';
      let schemaUrl = '';
      let finalJson: Record<string, unknown> | undefined;

      const consumeBlock = (block: string) => {
        const lines = block.split(/\r?\n/);
        const eventName = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() || '';
        const dataLines = lines
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart());
        if (!dataLines.length) return;
        const raw = dataLines.join('\n');
        if (!raw || raw === '[DONE]') return;
        let parsed: unknown = raw;
        try {
          parsed = JSON.parse(raw);
        } catch {
          parsed = raw;
        }
        qrCodeUrl = qrCodeUrl || findStringByKeys(parsed, ['qr_code_url', 'qrCodeUrl']) || '';
        schemaUrl = schemaUrl || findStringByKeys(parsed, ['schema_url', 'schemaUrl']) || '';
        if (eventName === 'error') {
          const envelope = parsed && typeof parsed === 'object' ? parsed as Envelope<unknown> : undefined;
          if ((options.retryOnUnauthorized ?? true) && isUnauthorizedPayload(parsed)) {
            throw new ApiError('登录状态已失效，请重新登录', { status: 401, code: envelope?.code });
          }
          throw new ApiError(toZhErrorMessage(extractErrorMessage(parsed) || 'Agent 对话失败'), { code: envelope?.code });
        }
        if (eventName === 'vision_error') {
          const text = extractSseText(parsed) || '图片处理失败，请重新上传或用文字描述地点';
          options.onVisionError?.(text);
          collected = collected ? `${collected}\n\n${text}` : text;
          options.onDelta?.(collected);
          return;
        }
        if (eventName === 'final') {
          if (parsed && typeof parsed === 'object') {
            const answer = (parsed as Record<string, unknown>).answer;
            if (answer && typeof answer === 'object' && !Array.isArray(answer)) {
              finalJson = answer as Record<string, unknown>;
              qrCodeUrl = qrCodeUrl || findStringByKeys(finalJson, ['qr_code_url', 'qrCodeUrl']) || '';
              schemaUrl = schemaUrl || findStringByKeys(finalJson, ['schema_url', 'schemaUrl']) || '';
            }
          }
          const finalText = extractFinalText(parsed);
          if (finalText && !collected) {
            collected = finalText;
            options.onDelta?.(collected);
          }
          return;
        }
        const text = extractSseText(parsed);
        if (!text) return;
        collected += text;
        options.onDelta?.(collected);
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\n\n/);
        buffer = blocks.pop() || '';
        blocks.forEach(consumeBlock);
      }
      if (buffer.trim()) consumeBlock(buffer);
      return { text: collected, qrCodeUrl: qrCodeUrl || undefined, schemaUrl: schemaUrl || undefined, finalJson };
    },
    async stop(sessionId: string) {
      return request<{ stopped: boolean; session_id: string }>(`/chat/session/${sessionId}/stop`, {
        method: 'POST'
      });
    },
    async listSessions(params?: { limit?: number; offset?: number; q?: string; scene?: string }) {
      return request<ChatSessionListResult>(`/chat/sessions${buildQuery(params)}`);
    },
    async listMessages(sessionId: string, params?: { limit?: number; offset?: number }) {
      return request<ChatMessageListResult>(`/chat/session/${sessionId}/messages${buildQuery(params)}`);
    },
    async renameSession(sessionId: string, data: { title: string }) {
      return request<{ updated: boolean; title: string }>(`/chat/session/${sessionId}`, {
        method: 'PATCH',
        body: data
      });
    },
    async deleteSession(sessionId: string) {
      return request<{ deleted: boolean }>(`/chat/session/${sessionId}`, {
        method: 'DELETE'
      });
    },
    getSessionId
  },
  decisions: {
    async quickFilterStart(payload: { query?: string }) {
      return request<QuickFilterState>('/decisions/quick-filter/start', {
        method: 'POST',
        body: payload
      });
    },
    async quickFilterAnswer(payload: { flow_id: string; answer: string }) {
      return request<QuickFilterState>('/decisions/quick-filter/answer', {
        method: 'POST',
        body: payload
      });
    }
  },
  plans: {
    async list() {
      return request<PlanRecord[]>('/plans');
    },
    async create(payload: {
      session_id?: string;
      title: string;
      plan_type?: string;
      status?: string;
      date_text?: string;
      source_text: string;
      qr_code_url?: string;
      schema_url?: string;
      plan_json: Record<string, unknown>;
    }) {
      return request<PlanRecord>('/plans', {
        method: 'POST',
        body: payload
      });
    },
    async remove(planId: string) {
      return request<{ deleted: boolean; id: string }>(`/plans/${planId}`, {
        method: 'DELETE'
      });
    }
  }
};
