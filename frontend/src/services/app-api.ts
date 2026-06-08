const ACCESS_TOKEN_KEY = 'app_access_token';
const REFRESH_TOKEN_KEY = 'app_refresh_token';
const CSRF_TOKEN_KEY = 'app_csrf_token';
const LOGIN_FLAG_KEY = 'isLoggedIn';
const ME_CACHE_TTL_MS = 30 * 1000;
const PUBLIC_CONFIG_CACHE_TTL_MS = 30 * 1000;

let refreshInFlight: Promise<boolean> | null = null;

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
    scene?: string;
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
    source?: 'env' | 'user_config';
    config_id?: string;
    is_default?: boolean;
}

export type AppLlmProviderType = 'openai_compatible' | 'anthropic';

export interface AppLlmProviderConfig {
    id: string;
    user_id: string;
    display_name: string;
    provider_type: AppLlmProviderType;
    base_url: string;
    api_key_hint: string;
    model_planner: string;
    model_writer?: string | null;
    model_vision_planner?: string | null;
    enabled: boolean;
    is_default: boolean;
    last_tested_at?: string | null;
    last_test_status?: string | null;
    last_test_error?: string | null;
}

export interface AppLlmProviderConfigInput {
    display_name: string;
    provider_type?: AppLlmProviderType;
    base_url: string;
    api_key?: string;
    model_planner: string;
    model_writer?: string | null;
    model_vision_planner?: string | null;
    enabled?: boolean;
    is_default?: boolean;
}

export interface AppLlmProviderConfigTestResult {
    status: 'success' | 'failed';
    error?: string | null;
}

export interface AppChatModelsResponse {
    models: AppChatModelOption[];
    default?: string;
    providers?: string[];
}

export interface AppChatAttachment {
    attachment_id: string;
    kind: 'image';
    object_key: string;
    filename: string;
    content_type: string;
    size_bytes: number;
}

export interface AppSkillInstallReport {
    allowed_tools: string[];
    denied_tools: Record<string, string>;
    warnings: string[];
    blocked_files: string[];
    risk_level: string;
}

export interface AppAgentSkill {
    id: string;
    name: string;
    version: string;
    description?: string;
    enabled: boolean;
    source: 'built_in' | 'imported' | string;
    tools: string[];
    install_report?: AppSkillInstallReport | null;
}

export interface AppSkillImportResult {
    skill_id: string;
    install_path: string;
    report: AppSkillInstallReport;
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

export interface AppIngredient {
    id: string;
    name: string;
    quantity?: number | null;
    unit?: string | null;
    quantity_text?: string;
    expiry_date?: string | null;
    source?: string;
}

export interface AppHomeChefRecipe {
    title: string;
    desc: string;
    time: string;
    cal: string;
    img: string;
    tag: string;
    ingredients?: string[];
    steps?: string[];
    method_markdown?: string;
}

export interface AppGroceryListItem {
    id: string;
    name: string;
    quantity?: number | null;
    unit?: string | null;
    category?: string | null;
    checked: boolean;
}

export interface AppGroceryList {
    id: string;
    title: string;
    source_recipe?: string;
    items: AppGroceryListItem[];
}

export interface AppDecisionResult {
    decision: {
        type: 'restaurant' | 'recipe' | 'fallback';
        title: string;
        confidence?: number;
        provider?: string;
        provider_id?: string;
        navigation_url?: string | null;
    };
    reasons: string[];
    actions: Array<{ type: string; label: string; url: string }>;
    meta?: Record<string, unknown>;
}

export interface AppQuickFilterState {
    flow_id: string;
    round: number;
    answers: Record<string, string>;
    done: boolean;
    next_question?: {
        slot: string;
        question: string;
        options: string[];
    } | null;
    result?: AppDecisionResult;
}

export interface AppGroupDecisionItem {
    id: string;
    title: string;
    item_type?: string;
    meta?: Record<string, unknown>;
    votes?: number;
}

export interface AppGroupDecisionSession {
    id: string;
    title: string;
    city?: string;
    status?: string;
    share_url: string;
    share_token?: string;
    items: AppGroupDecisionItem[];
}

export interface AppGroupDecisionResult {
    id: string;
    title: string;
    city?: string;
    status?: string;
    share_url: string;
    winner?: AppGroupDecisionItem | null;
    items: AppGroupDecisionItem[];
    total_votes: number;
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

export interface AppAuthFeatureCheck {
    enabled: boolean;
    ready: boolean;
    missing: string[];
}

export interface AppAuthPublicConfig {
    ready: boolean;
    auth: {
        password_login: boolean;
        register: boolean;
        otp_login: boolean;
        otp_register: boolean;
        password_reset: boolean;
        one_click: boolean;
        oauth: {
            github: boolean;
        };
        phone_enabled: boolean;
        email_enabled: boolean;
    };
    checks: Record<string, AppAuthFeatureCheck>;
}

export interface AppAuthMethods {
    email_bound: boolean;
    phone_bound: boolean;
    oauth_providers: string[];
    github_bound: boolean;
    phone_enabled?: boolean;
    email_enabled?: boolean;
    oauth_enabled?: {
        github?: boolean;
    };
}

// ---- Eval Workbench Types ----

export interface EvalAccessStatus {
    allowed: boolean;
    user_id?: string;
    email?: string;
    phone?: string;
}

export interface EvalReportSummary {
    name: string;
    timestamp?: string;
    total_cases: number;
    total_trials: number;
    overall_success_rate: number;
    failed_cases: number;
    p0_failed_cases: number;
    duration_seconds: number;
    suite?: string;
    runner?: string;
    size_bytes?: number;
    modified_at?: number;
}

export interface EvalReportListResponse {
    source: 'db' | 'json';
    results_dir: string;
    reports: EvalReportSummary[];
}

export interface EvalTrial {
    trial_number: number;
    weighted_score?: number;
    actual_scene?: string;
    actual_worker?: string;
    tool_calls?: string[];
    failure_class?: string;
    error_reason?: string;
    error?: string;
    final_answer_preview?: string;
    threshold_failures?: Array<{ metric: string; expected: number; actual: number }>;
    missing_metrics?: string[];
    scores?: Record<string, number>;
    duration_ms?: number;
    trace_timeline?: EvalTraceEvent[];
}

export interface EvalTraceEvent {
    index: number;
    event_type: string;
    label?: string;
    tool_name?: string;
    duration_ms?: number;
    timestamp?: string;
    data?: Record<string, unknown>;
}

export interface EvalCaseResult {
    case_id: string;
    scene?: string;
    category?: string;
    priority?: string;
    task?: string;
    success_rate: number;
    avg_scores?: Record<string, number>;
    trials?: EvalTrial[];
}

export interface EvalReport {
    metadata?: {
        suite?: string;
        runner?: string;
        report_schema_version?: string;
        [key: string]: unknown;
    };
    timestamp?: string;
    total_cases?: number;
    total_trials?: number;
    overall_success_rate: number;
    category_breakdown?: Record<string, { success_rate: number }>;
    scene_breakdown?: Record<string, { success_rate: number }>;
    failure_summary?: Record<string, Record<string, number | { success_rate: number }>>;
    duration_seconds?: number;
    results?: EvalCaseResult[];
}

export interface EvalReportResponse {
    available: boolean;
    source: 'db' | 'json';
    results_dir: string;
    selected: string;
    reports: EvalReportSummary[];
    report?: EvalReport;
    message?: string;
}

export interface EvalCaseDetailResponse {
    source: 'db' | 'json';
    report?: string;
    case?: EvalCaseResult;
    trials?: EvalTrial[];
}

export interface EvalCompareDelta {
    overall_success_rate: number;
    failed_cases: number;
    p0_failed_cases: number;
    duration_seconds: number;
}

export interface EvalCompareResponse {
    source: 'db' | 'json';
    baseline: string;
    candidate: string;
    summary_delta: EvalCompareDelta;
    baseline_summary: EvalReportSummary;
    candidate_summary: EvalReportSummary;
    case_changes: {
        regressions: Array<{ case_id: string; baseline: EvalCaseResult; candidate: EvalCaseResult }>;
        fixes: Array<{ case_id: string; baseline: EvalCaseResult; candidate: EvalCaseResult }>;
        score_drops: Array<{ case_id: string; metric: string; baseline: number; candidate: number; delta: number }>;
        score_gains: Array<{ case_id: string; metric: string; baseline: number; candidate: number; delta: number }>;
    };
    scene_delta?: Record<string, { baseline: number; candidate: number; delta: number }>;
    category_delta?: Record<string, { baseline: number; candidate: number; delta: number }>;
    metric_delta?: Record<string, { baseline: number; candidate: number; delta: number }>;
}

export interface EvalJob {
    id: string;
    status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string;
    runner: 'fixture' | 'live' | string;
    suite: 'quick' | 'full' | 'live-smoke' | string;
    num_trials: number;
    base_url?: string | null;
    include_llm_judge?: boolean;
    outcome_verify?: boolean;
    persist_db?: boolean;
    require_db_persist?: boolean;
    output_dir?: string;
    report_name?: string | null;
    report_path?: string | null;
    exit_code?: number | null;
    pid?: number | null;
    requested_by?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    error?: string | null;
    created_at?: string | null;
    logs_tail?: string;
}

export interface EvalJobCreatePayload {
    runner: 'fixture' | 'live';
    suite: 'quick' | 'full' | 'live-smoke';
    num_trials: number;
    base_url?: string;
    include_llm_judge?: boolean;
    outcome_verify?: boolean;
    persist_db?: boolean;
    require_db_persist?: boolean;
}

export interface EvalJobListResponse {
    total: number;
    limit: number;
    offset: number;
    records: EvalJob[];
}

export interface EvalJobResponse {
    job: EvalJob;
}

// ---- Realtime Eval Types ----

export interface RealtimeEvalRecord {
    id: string;
    session_id: string;
    scene?: string;
    agent_id?: string;
    is_fallback: boolean;
    has_content: boolean;
    overall_quality: number;
    efficiency: number;
    schema_compliance: number;
    no_fallback: number;
    has_content_score: number;
    no_leak: number;
    tool_call_count: number;
    repeated_action_rate: number;
    tool_names: string[];
    total_duration_ms: number;
    error?: string;
    error_reason?: string;
    timestamp?: string;
}

export interface RealtimeEvalListResponse {
    total: number;
    records: RealtimeEvalRecord[];
}

export interface RealtimeEvalSummaryResponse {
    hours: number;
    total_evals: number;
    avg_quality: number;
    fallback_rate: number;
    no_content_rate: number;
    leak_rate: number;
    avg_efficiency: number;
    avg_schema_compliance: number;
    avg_duration_ms: number;
    quality_trend: Array<{ hour: string; avg_quality: number; count: number }>;
    scene_distribution: Record<string, number>;
}

export interface EvalDatasetSummary {
    suite: string;
    total_cases: number;
    by_scene: Record<string, number>;
    by_category: Record<string, number>;
    by_priority: Record<string, number>;
}

export interface EvalDatasetCase {
    case_id: string;
    task?: string;
    scene?: string;
    category?: string;
    priority?: string;
    difficulty?: string;
    tags?: string[];
    expectations_summary?: Record<string, unknown>;
    scoring_summary?: { metrics?: string[]; weights?: Record<string, number> };
}

export interface EvalDatasetListResponse {
    datasets: EvalDatasetSummary[];
}

export interface EvalDatasetCasesResponse {
    suite: string;
    total_cases: number;
    cases: EvalDatasetCase[];
}

export interface MonitoringOverviewResponse {
    window: string;
    total_runs: number;
    task_success_proxy: number;
    fallback_rate: number;
    user_visible_fallback_rate?: number;
    agent_fallback_rate?: number;
    environment_failure_rate?: number;
    tool_error_rate: number;
    provider_error_rate?: number;
    tool_timeout_rate?: number;
    tool_call_accuracy_proxy?: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
    latency_p99_ms?: number;
    token_input?: number;
    token_output?: number;
    token_cost: number;
    tool_cost: number;
    total_cost?: number;
    secret_leak_rate: number;
    policy_violation_rate: number;
    human_escalation_rate: number;
    cache_hit_rate?: number;
    unsafe_action_block_rate?: number;
    avg_steps?: number;
    repeated_action_rate?: number;
    recovery_rate?: number;
}

export interface MonitoringTraceRecord extends RealtimeEvalRecord {
    trace_id?: string;
    user_id?: string;
    worker?: string;
    status?: string;
    failure_class?: string;
    root_failure_class?: string;
    user_visible_fallback?: boolean;
    agent_fallback?: boolean;
    environment_failure?: boolean;
    started_at?: string;
    ended_at?: string;
    latency_ms?: number;
    model_provider?: string;
    model_name?: string;
}

export interface MonitoringTraceListResponse {
    total: number;
    limit: number;
    offset: number;
    records: MonitoringTraceRecord[];
}

export interface MonitoringTraceDetailResponse {
    run: MonitoringTraceRecord;
    events: Array<{ index: number; event_type: string; timestamp?: number; tool_name?: string; duration_ms?: number; data?: Record<string, unknown> }>;
    tool_calls: Array<{ tool_name: string; args?: Record<string, unknown>; success: boolean; error_reason?: string; latency_ms?: number; cost?: number }>;
    metrics: Record<string, number>;
    review?: MonitoringReview | null;
}

export interface MonitoringFailureResponse {
    window: string;
    total_runs: number;
    by_failure_class: Record<string, number>;
    by_scene: Record<string, number>;
    by_worker: Record<string, number>;
    by_tool: Record<string, number>;
    by_status: Record<string, number>;
    by_metric: Record<string, number>;
}

export interface MonitoringCostLatencyResponse {
    window: string;
    total_runs: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
    latency_avg_ms: number;
    token_input: number;
    token_output: number;
    cached_tokens: number;
    cache_miss_tokens: number;
    token_cost: number;
    tool_cost: number;
    total_cost: number;
    cache_hit_rate: number;
}

export interface MonitoringSafetyResponse {
    window: string;
    total_runs: number;
    unsafe_action_block_rate: number;
    secret_leak_rate: number;
    policy_violation_rate: number;
    human_escalation_rate: number;
    no_leak: number;
}

export interface MonitoringReview {
    run_id?: string;
    reviewer_id?: string | null;
    decision: string;
    reason?: string | null;
    failure_reason?: string | null;
    failure_tags?: string[];
    corrected_answer?: string | null;
    expected_behavior?: string | null;
    review_confidence?: number | null;
    dataset_candidate?: boolean;
    notes?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
}

export interface MonitoringReviewListResponse {
    total: number;
    limit: number;
    offset: number;
    records: Array<{ run: MonitoringTraceRecord; review: MonitoringReview }>;
}

// ── Outcome / Judge / Alert / Dataset Version / Experiment Management ──

export interface EvalOutcomeDetail {
    case_id: string;
    trial_number: number;
    verifier: string;
    score: number;
    passed: boolean;
    failures: string[];
    details: Record<string, unknown>;
}

export interface EvalRunOutcomesResponse {
    run_id: string;
    source: string;
    outcomes: EvalOutcomeDetail[];
}

export interface EvalJudgeResult {
    case_id: string;
    trial_number: number;
    metric: string;
    score: number | null;
    reason: string | null;
    confidence: number | null;
    rubric_version: string;
    judge_model: string | null;
    skipped_reason: string | null;
}

export interface EvalRunJudgeResultsResponse {
    run_id: string;
    source: string;
    judge_results: EvalJudgeResult[];
}

export interface EvalAlert {
    id: string;
    alert_type: string;
    severity: string;
    status: string;
    payload: Record<string, unknown>;
    created_at: string | null;
    acknowledged_at: string | null;
    acknowledged_by: string | null;
    resolved_at: string | null;
    resolved_by: string | null;
}

export interface EvalAlertListResponse {
    total: number;
    limit: number;
    offset: number;
    alerts: EvalAlert[];
}

export interface EvalDatasetVersion {
    id: string;
    name: string;
    version: string;
    suite: string;
    status: string;
    created_by: string | null;
    created_at: string | null;
    total_cases: number;
    by_scene: Record<string, number>;
    by_category: Record<string, number>;
    by_priority: Record<string, number>;
    by_source: Record<string, number>;
    by_owner: Record<string, number>;
}

export interface EvalDatasetVersionsResponse {
    dataset: string;
    versions: EvalDatasetVersion[];
}

export interface EvalDatasetCaseFromTraceResponse {
    dataset: string;
    version: string;
    case_id: string;
    source: string;
    scene: string | null;
    category: string | null;
    priority: string | null;
    owner: string | null;
    review_status: string;
    last_failed_at: string | null;
    created_at: string | null;
    case: Record<string, unknown>;
}

export interface JudgeAgreementResponse {
    agreement_rate: number | null;
    false_positive_rate: number | null;
    false_negative_rate: number | null;
    total_compared: number;
    by_dimension: Record<string, { agreement_rate: number | null; total: number }>;
    window_start: string;
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

type ApiScope = 'app' | 'agent' | 'internal';

function buildUrl(path: string, scope: ApiScope = 'app'): string {
    const normalized = path.startsWith('/') ? path : `/${path}`;
    if (scope === 'internal') {
        return `${getApiBaseUrl()}/api/v1/internal${normalized}`;
    }
    return `${getApiBaseUrl()}/api/v1/${scope}${normalized}`;
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

let meCache: { data: AppProfile; expiresAt: number } | null = null;
let meInFlight: Promise<AppProfile> | null = null;
let publicConfigCache: { data: AppAuthPublicConfig; expiresAt: number } | null = null;
let publicConfigInFlight: Promise<AppAuthPublicConfig> | null = null;

function clearMeCache(): void {
    meCache = null;
    meInFlight = null;
}

function clearPublicConfigCache(): void {
    publicConfigCache = null;
    publicConfigInFlight = null;
}

function setTokens(payload: AuthPayload | null | undefined): void {
    if (!payload) return;
    const accessToken = payload.access_token || payload.accessToken || payload.token;
    const refreshToken = payload.refresh_token || payload.refreshToken;
    const csrfToken = payload.csrf_token || payload.csrfToken;

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
    if (csrfToken) {
        localStorage.setItem(CSRF_TOKEN_KEY, csrfToken);
    }
}

function clearTokens(): void {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    localStorage.removeItem(CSRF_TOKEN_KEY);
    localStorage.removeItem(LOGIN_FLAG_KEY);
    clearMeCache();
}

async function getPublicConfig(options: { force?: boolean } = {}): Promise<AppAuthPublicConfig> {
    const { force = false } = options;
    const now = Date.now();

    if (!force && publicConfigCache && publicConfigCache.expiresAt > now) {
        return publicConfigCache.data;
    }

    if (!force && publicConfigInFlight) {
        return publicConfigInFlight;
    }

    publicConfigInFlight = request<AppAuthPublicConfig>('/auth/public-config', { auth: false })
        .then((data) => {
            publicConfigCache = {
                data,
                expiresAt: Date.now() + PUBLIC_CONFIG_CACHE_TTL_MS,
            };
            return data;
        })
        .finally(() => {
            publicConfigInFlight = null;
        });

    return publicConfigInFlight;
}

const ERROR_MESSAGE_ZH_MAP: Array<{ match: RegExp; zh: string }> = [
    { match: /invalid credentials/i, zh: '账号或密码错误' },
    { match: /account temporarily locked/i, zh: '账号已被临时锁定，请稍后再试' },
    { match: /refresh token replay detected/i, zh: '检测到登录状态异常，请重新登录' },
    { match: /refresh token revoked|refresh token invalid/i, zh: '登录状态已失效，请重新登录' },
    { match: /refresh token required/i, zh: '缺少登录凭证，请重新登录' },
    { match: /csrf token invalid/i, zh: '安全校验失败，请刷新后重试' },
    { match: /email or phone already exists/i, zh: '手机号或邮箱已注册' },
    { match: /email or phone required|account required/i, zh: '请填写手机号或邮箱' },
    { match: /phone required/i, zh: '请输入手机号' },
    { match: /code required/i, zh: '请输入验证码' },
    { match: /login code invalid or expired|register code invalid or expired|reset code invalid or expired|otp/i, zh: '验证码无效或已过期' },
    { match: /account must be a valid phone or email/i, zh: '账号格式不正确，请输入手机号或邮箱' },
    { match: /phone must match/i, zh: '手机号格式不正确' },
    { match: /password length must be between 8 and 64/i, zh: '密码长度需在 8 到 64 位之间' },
    { match: /password must contain at least one letter/i, zh: '密码需至少包含一个字母' },
    { match: /password must contain at least one number/i, zh: '密码需至少包含一个数字' },
    { match: /oauth provider unsupported|oauth provider not configured/i, zh: '第三方登录暂不可用，请联系管理员配置' },
    { match: /oauth state invalid/i, zh: '第三方登录状态已失效，请重新发起' },
    { match: /group decision link expired/i, zh: '分享链接已过期' },
    { match: /invalid share token/i, zh: '分享链接无效' },
    { match: /group decision already closed/i, zh: '该投票已结束' },
];

function toZhErrorMessage(message: string): string {
    const text = (message || '').trim();
    if (!text) return '请求失败，请稍后重试';
    for (const item of ERROR_MESSAGE_ZH_MAP) {
        if (item.match.test(text)) return item.zh;
    }
    return text;
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
        throw new ApiError(toZhErrorMessage(payload.message || 'Request failed'), {
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
    if (refreshInFlight) {
        return refreshInFlight;
    }

    refreshInFlight = (async () => {
        const refreshToken = getRefreshToken();
        const csrfToken = getCsrfToken();

        const headers: Record<string, string> = {
            'Content-Type': 'application/json'
        };
        if (csrfToken) {
            headers['x-csrf-token'] = csrfToken;
        }

        const response = await fetch(buildUrl('/auth/refresh'), {
            method: 'POST',
            headers,
            credentials: 'include',
            body: JSON.stringify(
                refreshToken
                    ? {
                          refresh_token: refreshToken,
                          refreshToken
                      }
                    : {}
            )
        });

        if (!response.ok) return false;

        const body = await parseResponseBody(response);
        const data = parseEnvelope<AuthPayload>(body, response.status);
        setTokens(data);
        return Boolean(getAccessToken());
    })().finally(() => {
        refreshInFlight = null;
    });

    return refreshInFlight;
}

interface RequestOptions {
    method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    body?: JsonValue;
    auth?: boolean;
    retryOnUnauthorized?: boolean;
    scope?: ApiScope;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const {
        method = 'GET',
        body,
        auth = true,
        retryOnUnauthorized = true,
        scope = 'app'
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

    if (method !== 'GET') {
        const csrfToken = getCsrfToken();
        if (csrfToken) {
            headers['x-csrf-token'] = csrfToken;
        }
    }

    const response = await fetch(buildUrl(path, scope), {
        method,
        headers,
        credentials: 'include',
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
        throw new ApiError(toZhErrorMessage(errorMessage), { status: response.status });
    }

    return parseEnvelope<T>(payload, response.status);
}

async function requestForm<T>(
    path: string,
    formData: FormData,
    options: { auth?: boolean; retryOnUnauthorized?: boolean; scope?: ApiScope } = {}
): Promise<T> {
    const { auth = true, retryOnUnauthorized = true, scope = 'app' } = options;
    const headers: Record<string, string> = {};

    if (auth) {
        const token = getAccessToken();
        if (token) {
            headers.Authorization = `Bearer ${token}`;
        }
    }

    const csrfToken = getCsrfToken();
    if (csrfToken) {
        headers['x-csrf-token'] = csrfToken;
    }

    const response = await fetch(buildUrl(path, scope), {
        method: 'POST',
        headers,
        credentials: 'include',
        body: formData
    });

    if (response.status === 401 && auth && retryOnUnauthorized) {
        const refreshed = await tryRefreshAccessToken();
        if (refreshed) {
            return requestForm<T>(path, formData, { ...options, retryOnUnauthorized: false });
        }
        clearTokens();
    }

    const payload = await parseResponseBody(response);
    if (!response.ok) {
        try {
            parseEnvelope(payload, response.status);
        } catch (error) {
            if (error instanceof ApiError) {
                throw error;
            }
        }
        throw new ApiError(`HTTP ${response.status}`, { status: response.status });
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

export const appConfigStore = {
    async get(options: { force?: boolean } = {}) {
        return getPublicConfig(options);
    },
    clear: clearPublicConfigCache,
};

export const appApi = {
    auth: {
        async register(payload: {
            name: string;
            password: string;
            phone?: string;
            email?: string;
        }) {
            const data = await request<AuthPayload | { tokens?: AuthPayload }>('/auth/register', {
                method: 'POST',
                auth: false,
                body: payload
            });
            const tokenPayload = (data as { tokens?: AuthPayload }).tokens || (data as AuthPayload);
            setTokens(tokenPayload);
            return data;
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
        },

        async logoutAll() {
            try {
                return await request<{ revoked: number }>('/auth/logout-all', {
                    method: 'POST',
                    auth: true
                });
            } finally {
                clearTokens();
            }
        },

        async publicConfig(options: { force?: boolean } = {}) {
            return getPublicConfig(options);
        },

        async registerRequestOtp(payload: { phone?: string; email?: string }) {
            return request<{ sent: boolean; provider?: string; debug_code?: string }>('/auth/register/request-otp', {
                method: 'POST',
                auth: false,
                body: payload,
            });
        },

        async registerConfirm(payload: { name?: string; password: string; code: string; phone?: string; email?: string }) {
            const data = await request<AuthPayload | { tokens?: AuthPayload }>('/auth/register/confirm', {
                method: 'POST',
                auth: false,
                body: payload,
            });
            const tokenPayload = (data as { tokens?: AuthPayload }).tokens || (data as AuthPayload);
            setTokens(tokenPayload);
            return data;
        },

        async loginOtpRequest(payload: { account: string }) {
            return request<{ sent: boolean; provider?: string; debug_code?: string }>('/auth/login/otp/request', {
                method: 'POST',
                auth: false,
                body: payload,
            });
        },

        async loginOtpConfirm(payload: { account: string; code: string }) {
            const data = await request<AuthPayload | { tokens?: AuthPayload }>('/auth/login/otp/confirm', {
                method: 'POST',
                auth: false,
                body: payload,
            });
            const tokenPayload = (data as { tokens?: AuthPayload }).tokens || (data as AuthPayload);
            setTokens(tokenPayload);
            return data;
        },

        async loginOneClick(payload: { token: string }) {
            const data = await request<AuthPayload | { tokens?: AuthPayload }>('/auth/login/one-click', {
                method: 'POST',
                auth: false,
                body: payload,
            });
            const tokenPayload = (data as { tokens?: AuthPayload }).tokens || (data as AuthPayload);
            setTokens(tokenPayload);
            return data;
        },

        async resetPasswordRequest(payload: { account: string }) {
            return request<{ sent: boolean }>('/auth/password/reset-request', {
                method: 'POST',
                auth: false,
                body: payload,
            });
        },

        async resetPasswordConfirm(payload: { account: string; code: string; newPassword: string }) {
            return request<{ updated: boolean }>('/auth/password/reset-confirm', {
                method: 'POST',
                auth: false,
                body: {
                    account: payload.account,
                    code: payload.code,
                    new_password: payload.newPassword,
                    newPassword: payload.newPassword,
                },
            });
        },

        async oauthStart(provider: 'github') {
            return request<{ provider: string; state: string; auth_url: string }>(`/auth/oauth/${provider}/start`, {
                auth: false,
            });
        },

        async oauthCallback(provider: 'github', payload: { code: string; state: string }) {
            const data = await request<AuthPayload & { oauth?: Record<string, unknown> }>(`/auth/oauth/${provider}/callback`, {
                method: 'POST',
                auth: false,
                body: payload,
            });
            setTokens(data);
            return data;
        },

        async oauthBind(provider: 'github', payload: { code: string; state: string }) {
            return request<{ bound: boolean; provider: string }>(`/auth/oauth/${provider}/bind`, {
                method: 'POST',
                auth: true,
                body: payload,
            });
        },

        async oauthUnbind(provider: 'github') {
            return request<{ removed: boolean; provider: string }>(`/auth/oauth/${provider}`, {
                method: 'DELETE',
                auth: true,
            });
        },

        async methods() {
            return request<AppAuthMethods>('/auth/methods', {
                auth: true
            });
        },

        async configCheck() {
            return request<{ ready: boolean; checks: Record<string, AppAuthFeatureCheck> }>('/auth/config-check', {
                auth: true
            });
        },

        async listSessions() {
            return request<{ items: Array<Record<string, unknown>> }>('/auth/sessions', { auth: true });
        },

        async events(limit = 50) {
            return request<{ items: Array<Record<string, unknown>> }>(`/auth/events?limit=${limit}`, {
                auth: true
            });
        },

        async revokeSession(sessionId: string) {
            return request<{ revoked: boolean; session_id: string }>(`/auth/sessions/${sessionId}`, {
                method: 'DELETE',
                auth: true
            });
        },

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

    decisions: {
        async blindbox(payload: {
            query?: string;
            city?: string;
            lat?: number;
            lng?: number;
            budget_level?: number;
            scene?: string;
        }) {
            return request<AppDecisionResult>('/decisions/blindbox', {
                method: 'POST',
                body: payload
            });
        },
        async quickFilterStart(payload: { query?: string } = {}) {
            return request<AppQuickFilterState>('/decisions/quick-filter/start', {
                method: 'POST',
                body: payload
            });
        },
        async quickFilterAnswer(payload: {
            flow_id: string;
            answer: string;
            city?: string;
            lat?: number;
            lng?: number;
            budget_level?: number;
        }) {
            return request<AppQuickFilterState>('/decisions/quick-filter/answer', {
                method: 'POST',
                body: payload
            });
        }
    },

    groupDecisions: {
        async create(payload: {
            title?: string;
            city?: string;
            expires_hours?: number;
            as_draft?: boolean;
            options: Array<{ title: string; item_type?: string; meta?: Record<string, unknown> }>;
        }) {
            return request<AppGroupDecisionSession>('/group-decisions', {
                method: 'POST',
                body: payload
            });
        },
        async open(session_id: string) {
            return request<{ id: string; status: string; share_url: string }>(`/group-decisions/${session_id}/open`, {
                method: 'POST'
            });
        },
        async vote(payload: {
            session_id: string;
            item_id: string;
            voter_name: string;
            voter_key: string;
            token: string;
            note?: string;
        }) {
            const query = new URLSearchParams({ token: payload.token }).toString();
            return request<{ ok: boolean; session_id: string; item_id: string; changed: boolean }>(
                `/group-decisions/${payload.session_id}/vote?${query}`,
                {
                    method: 'POST',
                    body: {
                        item_id: payload.item_id,
                        voter_name: payload.voter_name,
                        voter_key: payload.voter_key,
                        note: payload.note
                    }
                }
            );
        },
        async result(sessionId: string, token: string) {
            const query = new URLSearchParams({ token }).toString();
            return request<AppGroupDecisionResult>(`/group-decisions/${sessionId}/result?${query}`);
        }
    },

    fridge: {
        async listIngredients() {
            return request<AppIngredient[]>('/fridge/ingredients');
        },
        async addIngredient(payload: {
            name: string;
            quantity?: number;
            unit?: string;
            expiry_date?: string;
            source?: string;
        }) {
            return request<AppIngredient>('/fridge/ingredients', { method: 'POST', body: payload });
        },
        async deleteIngredient(id: string) {
            return request<{ deleted: boolean }>(`/fridge/ingredients/${id}`, { method: 'DELETE' });
        }
    },

    homeChef: {
        async generateRecipes(payload: { ingredients?: string[]; count?: number }) {
            return request<{ recipes: AppHomeChefRecipe[] }>('/home-chef/recipes/generate', {
                method: 'POST',
                body: payload
            });
        }
    },

    grocery: {
        async createFromRecipe(payload: {
            recipe_name: string;
            required_items: Array<{ name: string; quantity?: number; unit?: string; category?: string }>;
        }) {
            return request<AppGroceryList>('/grocery-lists/from-recipe', { method: 'POST', body: payload });
        },
        async get(listId: string) {
            return request<AppGroceryList>(`/grocery-lists/${listId}`);
        },
        async toggleItem(listId: string, itemId: string, checked: boolean) {
            return request<AppGroceryListItem>(`/grocery-lists/${listId}/items/${itemId}`, {
                method: 'PATCH',
                body: { checked }
            });
        }
    },

    skills: {
        async list() {
            const data = await request<{ skills: AppAgentSkill[] }>('/skills', {
                scope: 'agent'
            });
            return data.skills || [];
        },
        async importUrl(url: string) {
            return request<AppSkillImportResult>('/skills/import/url', {
                method: 'POST',
                scope: 'agent',
                body: { url }
            });
        },
        async importZip(file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return requestForm<AppSkillImportResult>('/skills/import/zip', formData, {
                scope: 'agent'
            });
        },
        async uninstall(skillId: string) {
            return request<{ deleted: boolean; skill_id: string }>(`/skills/${encodeURIComponent(skillId)}`, {
                method: 'DELETE',
                scope: 'agent'
            });
        }
    },

    chat: {
        async listModels() {
            return request<AppChatModelsResponse>('/chat/models');
        },
        async listModelConfigs() {
            return request<AppLlmProviderConfig[]>('/chat/model-configs');
        },
        async createModelConfig(payload: AppLlmProviderConfigInput & { api_key: string }) {
            return request<AppLlmProviderConfig>('/chat/model-configs', {
                method: 'POST',
                body: payload
            });
        },
        async updateModelConfig(configId: string, payload: Partial<AppLlmProviderConfigInput>) {
            return request<AppLlmProviderConfig>(`/chat/model-configs/${encodeURIComponent(configId)}`, {
                method: 'PATCH',
                body: payload
            });
        },
        async deleteModelConfig(configId: string) {
            return request<{ deleted: boolean }>(`/chat/model-configs/${encodeURIComponent(configId)}`, {
                method: 'DELETE'
            });
        },
        async setDefaultModelConfig(configId: string) {
            return request<AppLlmProviderConfig>(`/chat/model-configs/${encodeURIComponent(configId)}/default`, {
                method: 'POST'
            });
        },
        async testModelConfig(payload: { config_id?: string; provider_type?: AppLlmProviderType; base_url?: string; api_key?: string; model?: string }) {
            return request<AppLlmProviderConfigTestResult>('/chat/model-configs/test', {
                method: 'POST',
                body: payload
            });
        },
        async createSession(payload: { title?: string; scene?: string } = {}) {
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
        async uploadAttachment(sessionId: string, file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return requestForm<AppChatAttachment>(`/chat/session/${sessionId}/attachments`, formData);
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
    },

    evaluations: {
        async checkAccess() {
            return request<EvalAccessStatus>('/eval-access', {
                scope: 'internal',
                auth: true,
            });
        },

        async listReports() {
            return request<EvalReportListResponse>('/eval-reports', {
                scope: 'internal',
                auth: true,
            });
        },

        async getReport(report: string = 'latest.json') {
            return request<EvalReportResponse>(`/eval-report?report=${encodeURIComponent(report)}`, {
                scope: 'internal',
                auth: true,
            });
        },

        async getCaseDetail(report: string, caseId: string) {
            return request<EvalCaseDetailResponse>(
                `/eval-report/case?report=${encodeURIComponent(report)}&case_id=${encodeURIComponent(caseId)}`,
                { scope: 'internal', auth: true }
            );
        },

        async compareReports(baseline: string, candidate: string) {
            return request<EvalCompareResponse>(
                `/eval-report/compare?baseline=${encodeURIComponent(baseline)}&candidate=${encodeURIComponent(candidate)}`,
                { scope: 'internal', auth: true }
            );
        },

        async createEvalJob(payload: EvalJobCreatePayload) {
            return request<EvalJobResponse>('/eval-jobs', {
                scope: 'internal',
                auth: true,
                method: 'POST',
                body: payload,
            });
        },

        async listEvalJobs(params: { status?: string; limit?: number; offset?: number } = {}) {
            const qs = new URLSearchParams();
            if (params.status) qs.set('status', params.status);
            if (params.limit) qs.set('limit', String(params.limit));
            if (params.offset) qs.set('offset', String(params.offset));
            return request<EvalJobListResponse>(
                `/eval-jobs?${qs.toString()}`,
                { scope: 'internal', auth: true }
            );
        },

        async getEvalJob(jobId: string) {
            return request<EvalJobResponse>(
                `/eval-jobs/${encodeURIComponent(jobId)}`,
                { scope: 'internal', auth: true }
            );
        },

        async cancelEvalJob(jobId: string) {
            return request<EvalJobResponse>(
                `/eval-jobs/${encodeURIComponent(jobId)}/cancel`,
                { scope: 'internal', auth: true, method: 'POST' }
            );
        },

        async listDatasets() {
            return request<EvalDatasetListResponse>('/eval-datasets', {
                scope: 'internal',
                auth: true,
            });
        },

        async listDatasetCases(dataset: string) {
            return request<EvalDatasetCasesResponse>(`/eval-datasets/${encodeURIComponent(dataset)}/cases`, {
                scope: 'internal',
                auth: true,
            });
        },

        async getMonitoringOverview(window: string = '1h') {
            return request<MonitoringOverviewResponse>(
                `/monitoring/overview?window=${encodeURIComponent(window)}`,
                { scope: 'internal', auth: true }
            );
        },

        async listMonitoringTraces(params: { limit?: number; offset?: number; sessionId?: string; userId?: string; scene?: string; worker?: string; tool?: string; status?: string } = {}) {
            const qs = new URLSearchParams();
            if (params.limit) qs.set('limit', String(params.limit));
            if (params.offset) qs.set('offset', String(params.offset));
            if (params.sessionId) qs.set('session_id', params.sessionId);
            if (params.userId) qs.set('user_id', params.userId);
            if (params.scene) qs.set('scene', params.scene);
            if (params.worker) qs.set('worker', params.worker);
            if (params.tool) qs.set('tool', params.tool);
            if (params.status) qs.set('status', params.status);
            return request<MonitoringTraceListResponse>(
                `/monitoring/traces?${qs.toString()}`,
                { scope: 'internal', auth: true }
            );
        },

        async getMonitoringTrace(runId: string) {
            return request<MonitoringTraceDetailResponse>(
                `/monitoring/traces/${encodeURIComponent(runId)}`,
                { scope: 'internal', auth: true }
            );
        },

        async getMonitoringFailures(window: string = '24h') {
            return request<MonitoringFailureResponse>(
                `/monitoring/failures?window=${encodeURIComponent(window)}`,
                { scope: 'internal', auth: true }
            );
        },

        async getMonitoringCostLatency(window: string = '24h') {
            return request<MonitoringCostLatencyResponse>(
                `/monitoring/cost-latency?window=${encodeURIComponent(window)}`,
                { scope: 'internal', auth: true }
            );
        },

        async getMonitoringSafety(window: string = '24h') {
            return request<MonitoringSafetyResponse>(
                `/monitoring/safety?window=${encodeURIComponent(window)}`,
                { scope: 'internal', auth: true }
            );
        },

        async listMonitoringReviews(params: { decision?: string; limit?: number; offset?: number } = {}) {
            const qs = new URLSearchParams();
            if (params.decision) qs.set('decision', params.decision);
            if (params.limit) qs.set('limit', String(params.limit));
            if (params.offset) qs.set('offset', String(params.offset));
            return request<MonitoringReviewListResponse>(
                `/monitoring/reviews?${qs.toString()}`,
                { scope: 'internal', auth: true }
            );
        },

        async listRealtimeEvals(params: { limit?: number; offset?: number; scene?: string; minQuality?: number } = {}) {
            const qs = new URLSearchParams();
            if (params.limit) qs.set('limit', String(params.limit));
            if (params.offset) qs.set('offset', String(params.offset));
            if (params.scene) qs.set('scene', params.scene);
            if (params.minQuality !== undefined) qs.set('min_quality', String(params.minQuality));
            return request<RealtimeEvalListResponse>(
                `/realtime-eval/recent?${qs.toString()}`,
                { scope: 'internal', auth: true }
            );
        },

        async getRealtimeEvalSummary(hours: number = 24) {
            return request<RealtimeEvalSummaryResponse>(
                `/realtime-eval/summary?hours=${hours}`,
                { scope: 'internal', auth: true }
            );
        },

        // ── Outcome / Judge / Alert / Dataset Versions ──

        async getEvalRunOutcomes(runId: string) {
            return request<EvalRunOutcomesResponse>(
                `/eval-runs/${encodeURIComponent(runId)}/outcomes`,
                { scope: 'internal', auth: true }
            );
        },

        async getEvalRunJudgeResults(runId: string) {
            return request<EvalRunJudgeResultsResponse>(
                `/eval-runs/${encodeURIComponent(runId)}/judge-results`,
                { scope: 'internal', auth: true }
            );
        },

        async getJudgeHumanAgreement(runId?: string, window: string = '30d') {
            const qs = new URLSearchParams();
            qs.set('window', window);
            const path = runId
                ? `/eval-runs/${encodeURIComponent(runId)}/judge-agreement?${qs.toString()}`
                : `/eval-runs/judge-agreement?${qs.toString()}`;
            return request<JudgeAgreementResponse>(path, { scope: 'internal', auth: true });
        },

        async listEvalAlerts(params: { status?: string; limit?: number; offset?: number } = {}) {
            const qs = new URLSearchParams();
            if (params.status) qs.set('status', params.status);
            if (params.limit) qs.set('limit', String(params.limit));
            if (params.offset) qs.set('offset', String(params.offset));
            return request<EvalAlertListResponse>(
                `/eval-alerts?${qs.toString()}`,
                { scope: 'internal', auth: true }
            );
        },

        async acknowledgeEvalAlert(alertId: string) {
            return request<{ alert: EvalAlert }>(
                `/eval-alerts/${encodeURIComponent(alertId)}/ack`,
                { scope: 'internal', auth: true, method: 'POST' }
            );
        },

        async resolveEvalAlert(alertId: string) {
            return request<{ alert: EvalAlert }>(
                `/eval-alerts/${encodeURIComponent(alertId)}/resolve`,
                { scope: 'internal', auth: true, method: 'POST' }
            );
        },

        async listDatasetVersions(dataset: string) {
            return request<EvalDatasetVersionsResponse>(
                `/eval-datasets/${encodeURIComponent(dataset)}/versions`,
                { scope: 'internal', auth: true }
            );
        },

        async createDatasetVersion(dataset: string, payload: { version: string; status?: string; created_by?: string }) {
            return request<EvalDatasetVersion>(
                `/eval-datasets/${encodeURIComponent(dataset)}/versions`,
                { scope: 'internal', auth: true, method: 'POST', body: payload }
            );
        },

        async createDatasetCaseFromTrace(dataset: string, payload: { run_id: string; version?: string; priority?: string; category?: string; owner?: string }) {
            return request<EvalDatasetCaseFromTraceResponse>(
                `/eval-datasets/${encodeURIComponent(dataset)}/cases/from-trace`,
                { scope: 'internal', auth: true, method: 'POST', body: payload }
            );
        },

        async submitMonitoringReview(runId: string, payload: {
            decision: string;
            reason?: string;
            notes?: string;
            failure_reason?: string;
            failure_tags?: string[];
            corrected_answer?: string;
            expected_behavior?: string;
            review_confidence?: number;
            dataset_candidate?: boolean;
        }) {
            return request<{ review: MonitoringReview }>(
                `/monitoring/reviews/${encodeURIComponent(runId)}`,
                { scope: 'internal', auth: true, method: 'POST', body: payload }
            );
        },

        // ── Experiment Management ──

        async updateEvalRunExperiment(runId: string, payload: {
            baseline_pin?: string;
            tags_json?: Record<string, unknown>;
            notes?: string;
            owner?: string;
            release_marker?: string;
        }) {
            return request<{ run_id: string; updated: string[] }>(
                `/eval-runs/${encodeURIComponent(runId)}/experiment`,
                { scope: 'internal', auth: true, method: 'PATCH', body: payload }
            );
        },

        async getHubOverview(window: string = '24h') {
            return request<any>(`/eval-hub/overview?window=${encodeURIComponent(window)}`, { scope: 'internal', auth: true });
        },

        async listHubLiveSessions(params: { window?: string; limit?: number; offset?: number } = {}) {
            const qs = new URLSearchParams();
            qs.set('window', params.window || '24h');
            if (params.limit) qs.set('limit', String(params.limit));
            if (params.offset) qs.set('offset', String(params.offset));
            return request<any>(`/eval-hub/live-sessions?${qs.toString()}`, { scope: 'internal', auth: true });
        },

        async getHubLiveSession(sessionId: string) {
            return request<any>(`/eval-hub/live-sessions/${encodeURIComponent(sessionId)}`, { scope: 'internal', auth: true });
        },

        async listHubTraces(params: { window?: string; scene?: string; status?: string; limit?: number; offset?: number } = {}) {
            const qs = new URLSearchParams();
            qs.set('window', params.window || '24h');
            if (params.scene) qs.set('scene', params.scene);
            if (params.status) qs.set('status', params.status);
            if (params.limit) qs.set('limit', String(params.limit));
            if (params.offset) qs.set('offset', String(params.offset));
            return request<any>(`/eval-hub/traces?${qs.toString()}`, { scope: 'internal', auth: true });
        },

        async getHubTrace(traceId: string) {
            return request<any>(`/eval-hub/traces/${encodeURIComponent(traceId)}`, { scope: 'internal', auth: true });
        },

        async createHubDatasetCaseFromTrace(traceId: string, payload: Record<string, unknown>) {
            return request<any>(`/eval-hub/traces/${encodeURIComponent(traceId)}/dataset-cases`, {
                scope: 'internal',
                auth: true,
                method: 'POST',
                body: payload,
            });
        },

        async listHubDatasets() {
            return request<any>('/eval-hub/datasets', { scope: 'internal', auth: true });
        },

        async createHubDataset(payload: Record<string, unknown>) {
            return request<any>('/eval-hub/datasets', { scope: 'internal', auth: true, method: 'POST', body: payload });
        },

        async listHubDatasetCases(dataset: string, version?: string) {
            const qs = new URLSearchParams();
            if (version) qs.set('version', version);
            const query = qs.toString();
            return request<any>(`/eval-hub/datasets/${encodeURIComponent(dataset)}/cases${query ? `?${query}` : ''}`, { scope: 'internal', auth: true });
        },

        async createHubDatasetCase(dataset: string, payload: Record<string, unknown>) {
            return request<any>(`/eval-hub/datasets/${encodeURIComponent(dataset)}/cases`, {
                scope: 'internal',
                auth: true,
                method: 'POST',
                body: payload,
            });
        },

        async listHubDatasetVersions(dataset: string) {
            return request<any>(`/eval-hub/datasets/${encodeURIComponent(dataset)}/versions`, { scope: 'internal', auth: true });
        },

        async createHubDatasetVersion(dataset: string, payload: Record<string, unknown>) {
            return request<any>(`/eval-hub/datasets/${encodeURIComponent(dataset)}/versions`, {
                scope: 'internal',
                auth: true,
                method: 'POST',
                body: payload,
            });
        },

        async listHubExperiments() {
            return request<any>('/eval-hub/experiments', { scope: 'internal', auth: true });
        },

        async createHubExperiment(payload: Record<string, unknown>) {
            return request<any>('/eval-hub/experiments', { scope: 'internal', auth: true, method: 'POST', body: payload });
        },

        async getHubExperiment(experimentId: string) {
            return request<any>(`/eval-hub/experiments/${encodeURIComponent(experimentId)}`, { scope: 'internal', auth: true });
        },

        async addHubExperimentRun(experimentId: string, payload: Record<string, unknown>) {
            return request<any>(`/eval-hub/experiments/${encodeURIComponent(experimentId)}/runs`, {
                scope: 'internal',
                auth: true,
                method: 'POST',
                body: payload,
            });
        },

        async compareHubExperiment(experimentId: string) {
            return request<any>(`/eval-hub/experiments/${encodeURIComponent(experimentId)}/compare`, { scope: 'internal', auth: true });
        },

        async listHubEvaluators() {
            return request<any>('/eval-hub/evaluators', { scope: 'internal', auth: true });
        },

        async createHubEvaluator(payload: Record<string, unknown>) {
            return request<any>('/eval-hub/evaluators', { scope: 'internal', auth: true, method: 'POST', body: payload });
        },

        async createHubPlaygroundRun(payload: Record<string, unknown>) {
            return request<any>('/eval-hub/playground/runs', { scope: 'internal', auth: true, method: 'POST', body: payload });
        },

        async getHubPlaygroundRun(runId: string) {
            return request<any>(`/eval-hub/playground/runs/${encodeURIComponent(runId)}`, { scope: 'internal', auth: true });
        },

        async listHubSimulationScenarios() {
            return request<any>('/eval-hub/simulations/scenarios', { scope: 'internal', auth: true });
        },

        async createHubSimulationScenario(payload: Record<string, unknown>) {
            return request<any>('/eval-hub/simulations/scenarios', { scope: 'internal', auth: true, method: 'POST', body: payload });
        },

        async createHubSimulationRun(scenarioId: string) {
            return request<any>(`/eval-hub/simulations/scenarios/${encodeURIComponent(scenarioId)}/runs`, { scope: 'internal', auth: true, method: 'POST' });
        },

        async listHubAnnotationQueue(params: { decision?: string; limit?: number; offset?: number } = {}) {
            const qs = new URLSearchParams();
            qs.set('decision', params.decision || 'pending');
            if (params.limit) qs.set('limit', String(params.limit));
            if (params.offset) qs.set('offset', String(params.offset));
            return request<any>(`/eval-hub/annotation/queue?${qs.toString()}`, { scope: 'internal', auth: true });
        },

        async updateHubAnnotation(runId: string, payload: Record<string, unknown>) {
            return request<any>(`/eval-hub/annotation/${encodeURIComponent(runId)}`, {
                scope: 'internal',
                auth: true,
                method: 'POST',
                body: payload,
            });
        }
    }
};
