import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Activity,
    AlertTriangle,
    ArrowRight,
    BookOpen,
    CheckCircle2,
    ClipboardCheck,
    Clock3,
    Cpu,
    Database,
    FlaskConical,
    GitCompare,
    Gauge,
    Layers3,
    LineChart,
    ListChecks,
    Search,
    ShieldCheck,
    Sparkles,
    TerminalSquare,
    WalletCards,
    XCircle,
} from 'lucide-react';

import EvaluationAccessGate from '@/components/EvaluationAccessGate';
import { ApiError, appApi } from '@/services/app-api';

type BusinessView =
    | 'quality'
    | 'sessions'
    | 'process'
    | 'failures'
    | 'cost'
    | 'safety'
    | 'reviews'
    | 'offline'
    | 'data'
    | 'expert';

type Filters = {
    window: string;
    environment: string;
    model: string;
    scene: string;
};

type Tone = 'good' | 'warn' | 'bad' | 'info' | 'neutral';

const DEFAULT_FILTERS: Filters = {
    window: '24h',
    environment: 'local',
    model: 'all',
    scene: '',
};

const NAV_ITEMS: Array<{ key: BusinessView; title: string; subtitle: string; icon: React.ElementType }> = [
    { key: 'quality', title: '质量总览', subtitle: 'Overview', icon: Gauge },
    { key: 'sessions', title: '会话质检', subtitle: 'Live Sessions', icon: Activity },
    { key: 'process', title: '执行过程', subtitle: 'Trace 执行轨迹', icon: Layers3 },
    { key: 'failures', title: '问题归因', subtitle: 'Failure Analysis', icon: AlertTriangle },
    { key: 'cost', title: '成本性能', subtitle: 'Cost & Latency', icon: WalletCards },
    { key: 'safety', title: '安全治理', subtitle: 'Safety', icon: ShieldCheck },
    { key: 'reviews', title: '人工审核', subtitle: 'Human Review', icon: ClipboardCheck },
    { key: 'offline', title: '离线评测', subtitle: 'Regression Eval', icon: FlaskConical },
    { key: 'data', title: '数据闭环', subtitle: 'Dataset & Experiment', icon: Database },
    { key: 'expert', title: '专家模式', subtitle: 'Trace / JSON', icon: TerminalSquare },
];

const FAILURE_COPY: Record<string, { title: string; action: string; tone: Tone }> = {
    provider_auth: { title: '模型服务鉴权失败', action: '检查模型 API Key、账号权限和后端环境变量。', tone: 'bad' },
    provider_billing_unavailable: { title: '模型余额不可用', action: '充值、更新套餐或切换可用模型后再跑真实对话探针。', tone: 'bad' },
    provider_timeout: { title: '模型响应超时', action: '检查模型服务状态，必要时降低任务复杂度或调大超时时间。', tone: 'warn' },
    provider_rate_limit: { title: '模型限流', action: '降低并发、切换模型或增加额度。', tone: 'warn' },
    provider_model_error: { title: '模型服务异常', action: '查看 provider 返回错误，确认模型名和服务可用性。', tone: 'bad' },
    tool_api_error: { title: '工具接口失败', action: '检查外部 API、网络、参数和错误日志。', tone: 'bad' },
    tool_timeout: { title: '工具调用超时', action: '检查工具服务延迟，增加缓存或超时降级。', tone: 'warn' },
    tool_empty_result: { title: '工具没有返回有效结果', action: '检查查询条件、数据源覆盖和兜底回答。', tone: 'warn' },
    tool_bad_args: { title: '工具参数不正确', action: '检查 Agent 工具选择和参数生成逻辑。', tone: 'bad' },
    agent_routing_error: { title: 'Agent 路由判断错误', action: '补充路由样例，检查 scene/worker 规则。', tone: 'bad' },
    agent_schema_error: { title: '回答结构不合规', action: '检查 final schema、结构化输出和兜底格式。', tone: 'bad' },
    agent_low_quality: { title: '回答质量偏低', action: '查看用户目标、证据、约束和最终答案是否匹配。', tone: 'warn' },
    safety_policy_violation: { title: '安全或合规风险', action: '优先人工复核，检查敏感信息、策略违规和高风险动作。', tone: 'bad' },
    eval_framework_error: { title: '评测系统异常', action: '检查评测指标、数据结构和后台任务日志。', tone: 'warn' },
    none: { title: '未发现明确失败', action: '无需处理；可抽样复核高成本或低分会话。', tone: 'good' },
};

const SPAN_COPY: Record<string, { title: string; icon: React.ElementType; tone: Tone }> = {
    llm_call: { title: '调用模型理解和生成', icon: Cpu, tone: 'info' },
    tool_call: { title: '调用外部工具', icon: TerminalSquare, tone: 'info' },
    router: { title: '判断场景和执行器', icon: GitCompare, tone: 'neutral' },
    planner: { title: '规划下一步动作', icon: ListChecks, tone: 'neutral' },
    executor: { title: '执行并生成结果', icon: CheckCircle2, tone: 'good' },
    guardrail: { title: '安全检查或异常处理', icon: ShieldCheck, tone: 'warn' },
    retrieval: { title: '检索证据或上下文', icon: Search, tone: 'info' },
};

const METRIC_COPY: Record<string, string> = {
    task_success_proxy: '任务完成率',
    partial_success_proxy: '部分完成率',
    overall_quality: '综合质量',
    tool_call_accuracy_proxy: '工具调用准确率',
    tool_error_rate: '工具失败率',
    provider_error_rate: '模型服务失败率',
    user_visible_fallback_rate: '用户可见兜底率',
    agent_fallback_rate: 'Agent 兜底率',
    environment_failure_rate: '环境失败率',
    schema_compliance: '结构合规',
    no_leak: '无敏感泄露',
    repeated_action_rate: '重复动作率',
    recovery_rate: '出错恢复率',
    fallback_rate: '兜底回答率',
    latency_p95_ms: 'P95 响应耗时',
    latency_p99_ms: 'P99 响应耗时',
};

const SCORE_METRICS = [
    'overall_quality',
    'task_success_proxy',
    'partial_success_proxy',
    'schema_compliance',
    'constraint_satisfaction_rule',
    'tool_call_accuracy_proxy',
    'no_leak',
    'recovery_rate',
    'repeated_action_rate',
    'tool_error_rate',
    'provider_error_rate',
];

const sceneLabel = (value?: string | null) => {
    const map: Record<string, string> = {
        home_chef: '在家做饭',
        cook_home: '在家做饭',
        eat_out: '出去吃',
        travel_planner: '旅行规划',
        route: '路线导航',
        chat: '通用聊天',
    };
    return map[value || ''] || value || '未识别';
};

const modelLabel = (value: any) => {
    const config = value?.model_config || {};
    return config.provider_value
        || config.model_planner
        || config.model_writer
        || value?.model_name
        || '未知模型';
};

const metricLabel = (value?: string | null) => METRIC_COPY[value || ''] || value || '未知指标';

const failureInfo = (value?: string | null) => FAILURE_COPY[value || 'none'] || {
    title: value || '未知问题',
    action: '查看执行过程和开发者详情定位原因。',
    tone: 'warn' as Tone,
};

const failureKey = (value: any) => String(value?.root_failure_class || value?.failure_class || 'none');

const spanInfo = (value?: string | null) => SPAN_COPY[value || ''] || {
    title: value || '执行事件',
    icon: Sparkles,
    tone: 'neutral' as Tone,
};

const fmtPct = (value: unknown) => {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : 'n/a';
};

const fmtMs = (value: unknown) => {
    const n = Number(value);
    return Number.isFinite(n) ? `${Math.round(n)}ms` : 'n/a';
};

const fmtToolMs = (value: unknown) => {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? `${Math.round(n)}ms` : '耗时未记录';
};

const fmtMoney = (value: unknown) => {
    const n = Number(value || 0);
    return `$${n.toFixed(n >= 10 ? 1 : 3)}`;
};

const fmtTime = (value: unknown) => value ? String(value).replace('T', ' ').slice(0, 16) : 'n/a';

const fmtStepTime = (value: unknown) => {
    if (!value) return '未记录';
    if (typeof value === 'number') return new Date(value * 1000).toLocaleTimeString();
    return String(value).replace('T', ' ').slice(0, 19);
};

const parseStepMs = (value: unknown) => {
    if (!value) return null;
    if (typeof value === 'number') return Number.isFinite(value) ? value * 1000 : null;
    const parsed = Date.parse(String(value));
    return Number.isFinite(parsed) ? parsed : null;
};

const displayValue = (value: unknown, fallback = 'n/a') => {
    if (value === null || value === undefined || value === '') return fallback;
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) return value.map((item) => displayValue(item, '')).filter(Boolean).join(', ') || fallback;
    if (typeof value === 'object') {
        const record = value as Record<string, unknown>;
        if (typeof record.decision === 'string') return record.decision;
        if (Array.isArray(record.reasons)) return record.reasons.map((item) => displayValue(item, '')).filter(Boolean).join('；') || fallback;
        try {
            return JSON.stringify(value);
        } catch {
            return fallback;
        }
    }
    return String(value);
};

const compactValue = (value: unknown, max = 220) => {
    const text = displayValue(value, '');
    if (!text) return '';
    return text.length > max ? `${text.slice(0, max)}...` : text;
};

const firstPresent = (...values: unknown[]) => values.find((value) => value !== null && value !== undefined && value !== '');

const stepInputSummary = (item: any) => compactValue(firstPresent(
    item.metadata?.data?.question,
    item.metadata?.data?.message,
    item.raw?.data?.question,
    item.raw?.data?.message,
    item.input?.args,
    item.input?.prompt,
    item.input?.messages,
    item.input?.provider || item.input?.model ? item.input : null,
    item.input,
    item.metadata?.data?.args,
    item.metadata?.data?.input,
    item.raw?.data?.args,
    item.raw?.data?.input,
));

const stepOutputSummary = (item: any) => compactValue(firstPresent(
    item.metadata?.data?.scene || item.metadata?.data?.worker || item.metadata?.data?.agent_id
        ? {
            scene: item.metadata?.data?.scene,
            worker: item.metadata?.data?.worker || item.metadata?.data?.agent_id,
            plan_type: item.metadata?.data?.plan_type,
            active_skills: item.metadata?.data?.active_skills,
            allowed_tools: item.metadata?.data?.allowed_tools,
            retrieved_memory_count: item.metadata?.data?.retrieved_memory_count,
        }
        : null,
    item.raw?.data?.scene || item.raw?.data?.worker || item.raw?.data?.agent_id
        ? {
            scene: item.raw?.data?.scene,
            worker: item.raw?.data?.worker || item.raw?.data?.agent_id,
            plan_type: item.raw?.data?.plan_type,
            active_skills: item.raw?.data?.active_skills,
            allowed_tools: item.raw?.data?.allowed_tools,
            retrieved_memory_count: item.raw?.data?.retrieved_memory_count,
        }
        : null,
    item.output?.output_preview,
    item.output?.preview,
    item.output?.result,
    item.output?.usage,
    item.output,
    item.metadata?.data?.output_preview,
    item.metadata?.data?.result_preview,
    item.metadata?.data?.answer,
    item.metadata?.data?.agent_result,
    item.raw?.data?.output_preview,
    item.raw?.data?.result_preview,
    item.raw?.data?.answer,
    item.raw?.data?.agent_result,
));

const stepErrorSummary = (item: any) => compactValue(firstPresent(
    item.error,
    item.output?.error,
    item.metadata?.data?.error_reason,
    item.metadata?.data?.error,
    item.raw?.data?.error_reason,
    item.raw?.data?.error,
), 260);

const renderCell = (value: React.ReactNode) => {
    if (React.isValidElement(value)) return value;
    if (value === null || value === undefined || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value as React.ReactNode;
    return <span className="font-mono text-[11px] text-slate-500">{displayValue(value)}</span>;
};

const toolLabel = (name?: string | null) => {
    const map: Record<string, string> = {
        food_decision: '餐饮决策',
        search_restaurants: '餐厅搜索',
        get_ip_location: '定位获取',
        geocode_location: '地点解析',
        get_weather: '天气查询',
        get_fridge_items: '冰箱食材',
        rag_search_recipes: '菜谱检索',
        search_recipes: '菜谱搜索',
        plan_route: '路线规划',
    };
    return map[name || ''] || name || '未知工具';
};

function summarizeTools(tools: any[]) {
    const grouped = new Map<string, {
        name: string;
        count: number;
        ok: number;
        failed: number;
        latency: number[];
        cost: number;
        errors: string[];
        failureClass?: string;
    }>();
    for (const tool of tools || []) {
        const name = String(tool.tool_name || tool.name || 'unknown');
        const row = grouped.get(name) || { name, count: 0, ok: 0, failed: 0, latency: [], cost: 0, errors: [] };
        row.count += 1;
        if (tool.success === false) row.failed += 1;
        else row.ok += 1;
        const latency = Number(tool.latency_ms);
        if (Number.isFinite(latency) && latency > 0) row.latency.push(latency);
        row.cost += Number(tool.cost || 0);
        if (tool.error_reason) row.errors.push(displayValue(tool.error_reason));
        if (tool.failure_class && tool.failure_class !== 'none') row.failureClass = tool.failure_class;
        grouped.set(name, row);
    }
    return Array.from(grouped.values());
}

const scoreTone = (value: unknown): Tone => {
    const n = Number(value);
    if (!Number.isFinite(n)) return 'neutral';
    if (n >= 0.8) return 'good';
    if (n >= 0.5) return 'warn';
    return 'bad';
};

const toneClass = (tone: Tone) => {
    if (tone === 'good') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    if (tone === 'warn') return 'border-amber-200 bg-amber-50 text-amber-800';
    if (tone === 'bad') return 'border-rose-200 bg-rose-50 text-rose-700';
    if (tone === 'info') return 'border-sky-200 bg-sky-50 text-sky-700';
    return 'border-slate-200 bg-slate-50 text-slate-600';
};

const getErrorMessage = (error: unknown) => error instanceof ApiError ? error.message : error instanceof Error ? error.message : '请求失败';

const metricTone = (key: string, value: unknown): Tone => {
    const number = Number(value);
    if (!Number.isFinite(number)) return 'neutral';
    if (key.includes('error') || key.includes('fallback') || key === 'repeated_action_rate') {
        if (number <= 0.02) return 'good';
        if (number <= 0.15) return 'warn';
        return 'bad';
    }
    return scoreTone(number);
};

const scoreMetricRows = (detail: any) => {
    const run = detail?.run || detail?.latest || detail || {};
    const metrics = detail?.metrics && typeof detail.metrics === 'object' ? detail.metrics : {};
    return SCORE_METRICS
        .map((key) => {
            const value = metrics[key] ?? run[key];
            if (value === undefined || value === null || value === '') return null;
            return {
                key,
                label: metricLabel(key),
                value: Number(value),
                tone: metricTone(key, value),
            };
        })
        .filter(Boolean) as Array<{ key: string; label: string; value: number; tone: Tone }>;
};

const compactObjectEntries = (value: unknown) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    return Object.entries(value as Record<string, unknown>)
        .filter(([, entryValue]) => entryValue !== null && entryValue !== undefined && entryValue !== '' && !(Array.isArray(entryValue) && !entryValue.length))
        .slice(0, 4);
};

function DatasetExpectationSummary({ value }: { value: unknown }) {
    const entries = compactObjectEntries(value);
    if (!entries.length) return <span className="text-[11px] font-semibold text-slate-400">未记录明确期望</span>;
    return (
        <div className="flex max-w-[340px] flex-wrap gap-1.5">
            {entries.map(([key, entryValue]) => (
                <span key={key} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-bold leading-relaxed text-slate-600">
                    {key}: {compactValue(entryValue, 48)}
                </span>
            ))}
        </div>
    );
}

function DatasetScoringSummary({ value }: { value: unknown }) {
    const metrics = Array.isArray((value as any)?.metrics)
        ? (value as any).metrics
        : Array.isArray(value)
            ? value
            : compactObjectEntries(value).map(([key]) => key);
    if (!metrics.length) return <span className="text-[11px] font-semibold text-slate-400">默认评分</span>;
    return (
        <div className="flex max-w-[260px] flex-wrap gap-1.5">
            {metrics.slice(0, 5).map((metric: unknown) => (
                <span key={String(metric)} className="rounded-md bg-sky-50 px-2 py-1 text-[11px] font-black text-sky-700">
                    {metricLabel(String(metric))}
                </span>
            ))}
        </div>
    );
}

const mergeDatasets = (...groups: any[][]) => {
    const map = new Map<string, any>();
    for (const group of groups) {
        for (const item of group || []) {
            const name = item?.name || item?.suite || item?.dataset;
            if (!name) continue;
            const key = `${name}:${item.version || 'file'}`;
            map.set(key, { ...(map.get(key) || {}), ...item, name, suite: item.suite || name });
        }
    }
    return Array.from(map.values()).sort((a, b) => {
        const order: Record<string, number> = { quick: 0, full: 1, 'live-smoke': 2 };
        return (order[a.name] ?? 10) - (order[b.name] ?? 10) || String(a.name).localeCompare(String(b.name));
    });
};

const sessionTitle = (value: any) => (
    value?.session_title
    || value?.title
    || value?.latest?.session_title
    || value?.latest?.title
    || value?.run?.session_title
    || value?.run?.title
    || value?.session_id
    || value?.sessionId
    || '未命名会话'
);

const originalSessionHref = (value: any) => {
    const run = value?.run || value?.latest || value || {};
    const sessionId = run.session_id || value?.session_id || value?.sessionId;
    if (!sessionId) return '';
    const scene = run.scene || value?.scene;
    const route = scene === 'travel_planner' ? '/travel-planner' : '/ai-chat';
    return `#${route}?session_id=${encodeURIComponent(String(sessionId))}`;
};

const openOriginalSession = (value: any) => {
    const href = originalSessionHref(value);
    if (href) window.open(href, '_blank', 'noopener,noreferrer');
};

function buildQualityOverviewView(overview: any) {
    const monitoring = overview?.monitoring || {};
    const safety = overview?.safety || {};
    return {
        totalRuns: monitoring.total_runs || overview?.total_runs || 0,
        successRate: monitoring.task_success_proxy ?? 0,
        failedRuns: Math.max(0, Math.round((monitoring.total_runs || 0) * (1 - Number(monitoring.task_success_proxy || 0)))),
        pendingReviews: overview?.pending_reviews || 0,
        toolErrorRate: monitoring.tool_error_rate || 0,
        providerErrorRate: monitoring.provider_error_rate || 0,
        userVisibleFallbackRate: monitoring.user_visible_fallback_rate ?? monitoring.fallback_rate ?? 0,
        agentFallbackRate: monitoring.agent_fallback_rate ?? monitoring.fallback_rate ?? 0,
        environmentFailureRate: monitoring.environment_failure_rate || 0,
        latencyP95: monitoring.latency_p95_ms || 0,
        totalCost: monitoring.total_cost || overview?.cost_latency?.total_cost || 0,
        safetyRisk: Math.max(Number(safety.secret_leak_rate || 0), Number(safety.policy_violation_rate || 0)),
        conclusion: Number(monitoring.task_success_proxy || 0) >= 0.8 ? '整体质量稳定' : '质量需要关注',
        nextAction: overview?.pending_reviews ? '先处理待人工审核会话，再查看问题归因。' : '继续观察异常、成本和低分会话。',
    };
}

function buildSessionInspectionView(session: any) {
    const latest = session?.latest || {};
    const turns = Array.isArray(session?.turns) ? session.turns : [];
    const latestTurn = turns[turns.length - 1] || {};
    const cost = latestTurn.cost || {};
    const tools = Array.isArray(latestTurn.tool_calls) ? latestTurn.tool_calls : [];
    const failedTools = tools.filter((tool: any) => !tool.success);
    const quality = latest.overall_quality ?? latest.score ?? 0;
    const failure = failureInfo(failureKey(latest));
    const environmentFailure = Boolean(latest.environment_failure);
    const agentFallback = Boolean(latest.agent_fallback);
    return {
        sessionId: session?.session_id,
        title: sessionTitle(session),
        status: latest.status || 'unknown',
        quality,
        scene: sceneLabel(latest.scene),
        worker: latest.worker || latest.agent_id || 'n/a',
        latency: latest.latency_ms,
        turnCount: session?.turn_count || turns.length || 0,
        tools,
        failedTools,
        totalTokens: cost.total_tokens || 0,
        totalCost: cost.total_cost || 0,
        conclusion: environmentFailure ? '这段会话被环境问题打断' : agentFallback ? '这段会话出现 Agent 兜底' : latest.status === 'completed' && Number(quality) >= 0.8 ? '这段会话完成得不错' : '这段会话需要复核',
        reason: failedTools.length ? `有 ${failedTools.length} 个工具调用失败` : failure.title,
        nextAction: failedTools.length ? '查看工具失败原因，必要时转人工审核。' : failure.action,
        turns,
    };
}

function buildExecutionTimelineView(detail: any) {
    const spans = Array.isArray(detail?.spans) ? detail.spans : [];
    const events = Array.isArray(detail?.events) ? detail.events : [];
    const run = detail?.run || {};
    const totalDuration = Number(run.latency_ms || run.total_duration_ms || 0);
    if (spans.length) {
        const spanStarts = spans.map((span: any) => parseStepMs(span.started_at));
        return spans.map((span: any, index: number) => {
            const info = spanInfo(span.span_type);
            const rawDuration = Number(span.duration_ms);
            const inferredFromBounds = parseStepMs(span.ended_at) != null && parseStepMs(span.started_at) != null
                ? Math.max(0, Number(parseStepMs(span.ended_at)) - Number(parseStepMs(span.started_at)))
                : null;
            const nextStart = spanStarts.slice(index + 1).find((value: number | null) => value != null);
            const inferredFromNext = spanStarts[index] != null && nextStart != null
                ? Math.max(0, Number(nextStart) - Number(spanStarts[index]))
                : null;
            const syntheticDuration = totalDuration > 0 ? Math.max(1, totalDuration / Math.max(spans.length, 1)) : null;
            const duration = Number.isFinite(rawDuration) && rawDuration > 0
                ? rawDuration
                : inferredFromBounds && inferredFromBounds > 0
                    ? inferredFromBounds
                    : inferredFromNext && inferredFromNext > 0
                        ? inferredFromNext
                        : syntheticDuration;
            return {
                id: span.id || `${span.span_type}-${index}`,
                index,
                title: info.title,
                subtitle: span.name || span.span_type,
                tone: span.status === 'error' ? 'bad' as Tone : info.tone,
                icon: info.icon,
                duration,
                durationEstimated: !(Number.isFinite(rawDuration) && rawDuration > 0),
                durationRatio: totalDuration > 0 && duration ? duration / totalDuration : null,
                status: span.status || 'ok',
                startedAt: span.started_at,
                endedAt: span.ended_at,
                inputSummary: stepInputSummary(span),
                outputSummary: stepOutputSummary(span),
                errorSummary: stepErrorSummary(span),
                detail: span.error || span.output?.output_preview || '',
                raw: span,
            };
        });
    }
    const eventStarts = events.map((event: any) => parseStepMs(event.timestamp));
    return events.map((event: any, index: number) => {
        const info = spanInfo(event.event_type);
        const rawDuration = Number(event.duration_ms);
        const nextStart = eventStarts.slice(index + 1).find((value: number | null) => value != null);
        const inferredFromNext = eventStarts[index] != null && nextStart != null
            ? Math.max(0, Number(nextStart) - Number(eventStarts[index]))
            : null;
        const syntheticDuration = totalDuration > 0 ? Math.max(1, totalDuration / Math.max(events.length, 1)) : null;
        const duration = Number.isFinite(rawDuration) && rawDuration > 0
            ? rawDuration
            : inferredFromNext && inferredFromNext > 0
                ? inferredFromNext
                : syntheticDuration;
        return {
            id: `${event.event_type}-${index}`,
            index,
            title: info.title,
            subtitle: event.tool_name || event.event_type,
            tone: event.event_type === 'error' ? 'bad' as Tone : info.tone,
            icon: info.icon,
            duration,
            durationEstimated: !(Number.isFinite(rawDuration) && rawDuration > 0),
            durationRatio: totalDuration > 0 && duration ? duration / totalDuration : null,
            status: event.event_type,
            startedAt: event.timestamp,
            endedAt: null,
            inputSummary: stepInputSummary({ raw: event, metadata: { data: event.data } }),
            outputSummary: stepOutputSummary({ raw: event, metadata: { data: event.data } }),
            errorSummary: stepErrorSummary({ raw: event, metadata: { data: event.data } }),
            detail: event.data?.message || event.data?.output_preview || '',
            raw: event,
        };
    });
}

function buildFailureInsightView(failures: any) {
    const groups = failures || {};
    const byClass = groups.by_failure_class || groups.failure_class || {};
    return Object.entries(byClass)
        .filter(([, count]) => Number(count) > 0)
        .map(([key, count]) => {
            const info = failureInfo(key);
            return { key, count: Number(count), ...info };
        });
}

function buildCostPerformanceView(cost: any) {
    return {
        totalRuns: cost?.total_runs || 0,
        latencyP50: cost?.latency_p50_ms || 0,
        latencyP95: cost?.latency_p95_ms || 0,
        latencyP99: cost?.latency_p99_ms || 0,
        inputTokens: cost?.token_input || 0,
        outputTokens: cost?.token_output || 0,
        cachedTokens: cost?.cached_tokens || 0,
        cacheMissTokens: cost?.cache_miss_tokens || 0,
        cacheHitRate: cost?.cache_hit_rate || 0,
        tokenCost: cost?.token_cost || 0,
        toolCost: cost?.tool_cost || 0,
        totalCost: cost?.total_cost || 0,
        byProvider: cost?.by_provider || {},
    };
}

function buildSafetyGovernanceView(safety: any) {
    return {
        totalRuns: safety?.total_runs || 0,
        unsafeBlockRate: safety?.unsafe_action_block_rate || 0,
        secretLeakRate: safety?.secret_leak_rate || 0,
        policyViolationRate: safety?.policy_violation_rate || 0,
        humanEscalationRate: safety?.human_escalation_rate || 0,
        noLeak: safety?.no_leak ?? (1 - Number(safety?.secret_leak_rate || 0)),
    };
}

export default function EvaluationWorkbench() {
    return (
        <EvaluationAccessGate>
            <QualityOperationsWorkbench />
        </EvaluationAccessGate>
    );
}

function QualityOperationsWorkbench() {
    const [view, setView] = useState<BusinessView>('quality');
    const [expertMode, setExpertMode] = useState(false);
    const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');

    const [overview, setOverview] = useState<any>(null);
    const [sessions, setSessions] = useState<any[]>([]);
    const [selectedSession, setSelectedSession] = useState<any>(null);
    const [traces, setTraces] = useState<any[]>([]);
    const [selectedTrace, setSelectedTrace] = useState<any>(null);
    const [failures, setFailures] = useState<any>(null);
    const [cost, setCost] = useState<any>(null);
    const [safety, setSafety] = useState<any>(null);
    const [reviews, setReviews] = useState<any>(null);
    const [reports, setReports] = useState<any[]>([]);
    const [jobs, setJobs] = useState<any[]>([]);
    const [datasets, setDatasets] = useState<any[]>([]);
    const [datasetCases, setDatasetCases] = useState<any[]>([]);
    const [experiments, setExperiments] = useState<any[]>([]);
    const [evaluators, setEvaluators] = useState<any[]>([]);
    const [componentRuns, setComponentRuns] = useState<any[]>([]);
    const [simulationScenarios, setSimulationScenarios] = useState<any[]>([]);
    const [judgeAgreement, setJudgeAgreement] = useState<any>(null);

    const loadCore = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [
                overviewData,
                sessionsData,
                tracesData,
                failuresData,
                costData,
                safetyData,
                reviewsData,
                reportData,
                jobData,
                datasetData,
                fileDatasetData,
                experimentData,
                evaluatorData,
                componentData,
                simulationData,
                judgeData,
            ] = await Promise.all([
                appApi.evaluations.getHubOverview(filters.window),
                appApi.evaluations.listHubLiveSessions({ window: filters.window, limit: 100 }),
                appApi.evaluations.listHubTraces({ window: filters.window, scene: filters.scene || undefined, limit: 100 }),
                appApi.evaluations.getMonitoringFailures(filters.window),
                appApi.evaluations.getMonitoringCostLatency(filters.window),
                appApi.evaluations.getMonitoringSafety(filters.window),
                appApi.evaluations.listMonitoringReviews({ decision: 'pending', limit: 100 }),
                appApi.evaluations.listReports(),
                appApi.evaluations.listEvalJobs({ limit: 50 }),
                appApi.evaluations.listHubDatasets(),
                appApi.evaluations.listDatasets(),
                appApi.evaluations.listHubExperiments(),
                appApi.evaluations.listHubEvaluators(),
                appApi.evaluations.listComponentRuns(),
                appApi.evaluations.listHubSimulationScenarios(),
                appApi.evaluations.getJudgeHumanAgreement(undefined, '30d'),
            ]);
            setOverview(overviewData);
            setSessions(sessionsData.records || []);
            setTraces(tracesData.records || []);
            setFailures(failuresData);
            setCost(costData);
            setSafety(safetyData);
            setReviews(reviewsData);
            setReports(reportData.reports || []);
            setJobs(jobData.records || []);
            const datasetRecords = mergeDatasets(fileDatasetData.datasets || [], datasetData.datasets || []);
            setDatasets(datasetRecords);
            setExperiments(experimentData.records || []);
            setEvaluators(evaluatorData.evaluators || []);
            setComponentRuns(componentData.records || []);
            setSimulationScenarios(simulationData.scenarios || []);
            setJudgeAgreement(judgeData || null);
            if (!datasetCases.length && datasetRecords.length) {
                try {
                    const firstDataset = datasetRecords[0]?.name || datasetRecords[0]?.suite || 'regression';
                    const cases = await appApi.evaluations.listDatasetCases(firstDataset);
                    setDatasetCases(cases.cases || []);
                } catch {
                    setDatasetCases([]);
                }
            }
        } catch (err) {
            setError(getErrorMessage(err));
        } finally {
            setLoading(false);
        }
    }, [filters.window, filters.scene, datasetCases.length]);

    useEffect(() => {
        loadCore();
    }, [loadCore]);

    useEffect(() => {
        const timer = window.setInterval(loadCore, 30000);
        return () => window.clearInterval(timer);
    }, [loadCore]);

    const quality = buildQualityOverviewView(overview);
    const activeNav = NAV_ITEMS.find((item) => item.key === view) || NAV_ITEMS[0];

    const openSession = async (sessionId: string) => {
        const detail = await appApi.evaluations.getHubLiveSession(sessionId);
        setSelectedSession(detail);
        setView('sessions');
    };

    const openTrace = async (traceId: string) => {
        const detail = await appApi.evaluations.getHubTrace(traceId);
        setSelectedTrace(detail);
        setView('process');
    };

    const content = useMemo(() => {
        if (view === 'quality') {
            return (
                <QualityOverview
                    quality={quality}
                    overview={overview}
                    sessions={sessions}
                    failures={failures}
                    onOpenSession={openSession}
                    onGo={setView}
                />
            );
        }
        if (view === 'sessions') {
            return (
                <SessionInspection
                    sessions={sessions}
                    selected={selectedSession}
                    expertMode={expertMode}
                    onOpenSession={openSession}
                    onOpenTrace={openTrace}
                />
            );
        }
        if (view === 'process') {
            return (
                <ExecutionProcess
                    traces={traces}
                    selected={selectedTrace}
                    expertMode={expertMode}
                    onOpenTrace={openTrace}
                    onAddToDataset={async (traceId) => {
                        await appApi.evaluations.createHubDatasetCaseFromTrace(traceId, { dataset: 'regression', version: 'draft', priority: 'p1' });
                        setMessage('已加入 regression draft 数据集');
                    }}
                    onSendReview={async (runId) => {
                        await appApi.evaluations.updateHubAnnotation(runId, { decision: 'needs_followup', reason: 'sent_from_trace' });
                        setMessage('已发送到人工审核');
                    }}
                />
            );
        }
        if (view === 'failures') {
            return <FailureInsights failures={failures} traces={traces} onOpenTrace={openTrace} />;
        }
        if (view === 'cost') {
            return <CostPerformance data={buildCostPerformanceView(cost)} />;
        }
        if (view === 'safety') {
            return <SafetyGovernance data={buildSafetyGovernanceView(safety)} />;
        }
        if (view === 'reviews') {
            return (
                <HumanReviews
                    data={reviews}
                    onRefresh={loadCore}
                    onOpenTrace={openTrace}
                />
            );
        }
        if (view === 'offline') {
            return <OfflineEvaluation reports={reports} jobs={jobs} datasets={datasets} onRefresh={loadCore} />;
        }
        if (view === 'data') {
            return (
                <DataFeedbackLoop
                    datasets={datasets}
                    cases={datasetCases}
                    experiments={experiments}
                    evaluators={evaluators}
                    componentRuns={componentRuns}
                    simulationScenarios={simulationScenarios}
                    judgeAgreement={judgeAgreement}
                    onRefresh={loadCore}
                />
            );
        }
        return <ExpertConsole overview={overview} sessions={sessions} traces={traces} selectedTrace={selectedTrace} selectedSession={selectedSession} failures={failures} cost={cost} safety={safety} />;
    }, [view, quality, overview, sessions, failures, selectedSession, expertMode, selectedTrace, traces, cost, safety, reviews, reports, jobs, datasets, datasetCases, experiments, evaluators, componentRuns, simulationScenarios, judgeAgreement, loadCore]);

    return (
        <div className="min-h-[100dvh] bg-[#edf2f7] text-slate-950">
            <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
                <div className="mx-auto flex max-w-[1480px] flex-wrap items-center gap-3 px-4 py-3 md:px-6">
                    <div className="flex min-w-[220px] items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-lg border border-slate-900 bg-slate-950 text-white shadow-sm">
                            <ClipboardCheck size={19} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2 text-[15px] font-black">
                                AgentEval Hub
                                <Badge tone="info">{filters.environment}</Badge>
                            </div>
                            <div className="text-[12px] font-semibold text-slate-500">质检运营台 · {filters.window}</div>
                        </div>
                    </div>

                    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                        <Select value={filters.environment} onChange={(e) => setFilters((v) => ({ ...v, environment: e.target.value }))}>
                            <option value="local">local</option>
                            <option value="staging">staging</option>
                            <option value="prod">prod</option>
                        </Select>
                        <Select value={filters.window} onChange={(e) => setFilters((v) => ({ ...v, window: e.target.value }))}>
                            <option value="5m">5m</option>
                            <option value="1h">1h</option>
                            <option value="24h">24h</option>
                            <option value="7d">7d</option>
                        </Select>
                        <Input value={filters.model} onChange={(e) => setFilters((v) => ({ ...v, model: e.target.value }))} aria-label="模型过滤" className="w-36" />
                        <Input value={filters.scene} onChange={(e) => setFilters((v) => ({ ...v, scene: e.target.value }))} aria-label="场景过滤" placeholder="场景" className="w-28" />
                    </div>

                    <div className="flex items-center gap-2">
                        <Segmented
                            value={expertMode ? 'expert' : 'business'}
                            options={[
                                ['business', '业务视图'],
                                ['expert', '专家视图'],
                            ]}
                            onChange={(value) => setExpertMode(value === 'expert')}
                        />
                        <Button onClick={loadCore}>刷新</Button>
                    </div>
                </div>
                <EvaluationTabs value={view} onChange={setView} />
            </header>

            <main className="mx-auto grid max-w-[1480px] gap-5 px-4 py-5 md:px-6">
                <PageHeader
                    title={activeNav.title}
                    subtitle={activeNav.subtitle}
                    description="默认看结论、原因和下一步；需要排障时再打开专家视图。"
                    badges={[
                        loading ? <Badge tone="info">同步中</Badge> : null,
                        message ? <Badge tone="good">{message}</Badge> : null,
                        error ? <Badge tone="bad">{error}</Badge> : null,
                        <Badge tone={scoreTone(quality.successRate)}>成功 {fmtPct(quality.successRate)}</Badge>,
                        <Badge tone={quality.pendingReviews ? 'warn' : 'good'}>待审 {quality.pendingReviews}</Badge>,
                        expertMode ? <Badge tone="warn">专家视图已开启</Badge> : null,
                    ]}
                />
                {content}
            </main>
        </div>
    );
}

function QualityOverview({
    quality,
    overview,
    sessions,
    failures,
    onOpenSession,
    onGo,
}: {
    quality: ReturnType<typeof buildQualityOverviewView>;
    overview: any;
    sessions: any[];
    failures: any;
    onOpenSession: (sessionId: string) => void;
    onGo: (view: BusinessView) => void;
}) {
    const insights = buildFailureInsightView(failures);
    return (
        <div className="grid gap-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-10">
                <MetricCard title="对话数" value={quality.totalRuns} hint="已采集会话轮次" />
                <MetricCard title="完成率" value={fmtPct(quality.successRate)} tone={scoreTone(quality.successRate)} hint="任务成功 proxy" />
                <MetricCard title="失败会话" value={quality.failedRuns} tone={quality.failedRuns ? 'warn' : 'good'} />
                <MetricCard title="待人工审核" value={quality.pendingReviews} tone={quality.pendingReviews ? 'warn' : 'good'} />
                <MetricCard title="工具失败" value={fmtPct(quality.toolErrorRate)} tone={Number(quality.toolErrorRate) > 0 ? 'bad' : 'good'} />
                <MetricCard title="Agent 兜底" value={fmtPct(quality.agentFallbackRate)} tone={Number(quality.agentFallbackRate) > 0 ? 'bad' : 'good'} />
                <MetricCard title="环境失败" value={fmtPct(quality.environmentFailureRate)} tone={Number(quality.environmentFailureRate) > 0 ? 'warn' : 'good'} />
                <MetricCard title="P95 耗时" value={fmtMs(quality.latencyP95)} tone={Number(quality.latencyP95) > 10000 ? 'warn' : 'neutral'} />
                <MetricCard title="总成本" value={fmtMoney(quality.totalCost)} />
                <MetricCard title="安全风险" value={fmtPct(quality.safetyRisk)} tone={Number(quality.safetyRisk) > 0 ? 'bad' : 'good'} />
            </div>

            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
                <Panel title="今天应该先看什么">
                    <div className="grid gap-3 p-4">
                        <ConclusionCard
                            title={quality.conclusion}
                            body={quality.nextAction}
                            tone={Number(quality.successRate) >= 0.8 ? 'good' : 'warn'}
                            action={<Button tone="light" onClick={() => onGo(quality.pendingReviews ? 'reviews' : 'failures')}>去处理 <ArrowRight size={14} /></Button>}
                        />
                        <div className="grid gap-3 md:grid-cols-3">
                            <ActionTile title="看会话" body="从真实用户会话开始，确认 Agent 是否完成任务。" onClick={() => onGo('sessions')} />
                            <ActionTile title="看问题" body="按模型、工具、Agent 质量和安全风险归因。" onClick={() => onGo('failures')} />
                            <ActionTile title="看成本" body="确认 token、工具调用和延迟是否异常。" onClick={() => onGo('cost')} />
                        </div>
                    </div>
                </Panel>
                <Panel title="最近待关注会话">
                    <div className="grid gap-2 p-4">
                        {sessions.slice(0, 5).map((item) => (
                            <div key={item.session_id} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-left transition hover:bg-white">
                                <button onClick={() => onOpenSession(item.session_id)} className="w-full text-left">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="truncate text-[13px] font-black text-slate-900">{sessionTitle(item)}</span>
                                    <Badge tone={scoreTone(item.latest_score)}>{fmtPct(item.latest_score)}</Badge>
                                </div>
                                <div className="mt-1 truncate font-mono text-[11px] font-semibold text-slate-400">{item.session_id}</div>
                                <div className="mt-2 text-[12px] font-semibold text-slate-600">
                                    {sceneLabel(item.scene)} · {item.turn_count || 0} 轮 · {fmtMs(item.latency_ms)}
                                </div>
                                </button>
                                <div className="mt-3">
                                    <Button tone="light" onClick={() => openOriginalSession(item)}>打开原始会话</Button>
                                </div>
                            </div>
                        ))}
                        {!sessions.length && <ExplainEmpty title="还没有线上会话" body="开启 REALTIME_EVAL_ENABLED 并完成一轮聊天后，这里会出现会话质检结果。" />}
                    </div>
                </Panel>
            </div>

            <Panel title="主要问题分布">
                <FailureBars insights={insights} emptyText="当前窗口内没有明确失败归因。" />
                {overview?.recent_eval_runs?.length > 0 && (
                    <div className="border-t border-slate-100 p-4">
                        <div className="mb-3 text-[12px] font-black text-slate-500">最近发布前回归检查</div>
                        <SimpleTable
                            headers={['报告', '范围', '通过率', '耗时']}
                            rows={overview.recent_eval_runs.slice(0, 5).map((run: any) => [
                                <span className="font-mono text-sky-700">{run.report_name}</span>,
                                `${run.suite || 'n/a'} / ${run.runner || 'n/a'}`,
                                <Badge tone={scoreTone(run.overall_success_rate)}>{fmtPct(run.overall_success_rate)}</Badge>,
                                `${Number(run.duration_seconds || 0).toFixed(2)}s`,
                            ])}
                        />
                    </div>
                )}
            </Panel>
        </div>
    );
}

function SessionInspection({
    sessions,
    selected,
    expertMode,
    onOpenSession,
    onOpenTrace,
}: {
    sessions: any[];
    selected: any;
    expertMode: boolean;
    onOpenSession: (sessionId: string) => void;
    onOpenTrace: (traceId: string) => void;
}) {
    const vm = selected ? buildSessionInspectionView(selected) : null;
    const latestTurn = selected?.turns?.[selected.turns.length - 1] || null;
    return (
        <div className="grid gap-5">
            <PageIntro
                title="左侧定位会话，右侧直接看结论"
                body="不用再滚到页面底部找详情。选中任意会话后，质检结论、每轮执行、工具调用和原始会话入口都会固定在右侧检查区。"
            />
            <WorkspaceSplit
                listTitle="会话列表"
                listHint="点击一行查看质检详情；按钮可直接跳回原始聊天。"
                detailTitle="会话质检结果"
                detailHint={selected ? sessionTitle(selected) : '等待选择会话'}
                list={(
                    <SimpleTable
                        headers={['会话', '最近场景', '结果', '根因', '质量', '轮次', '耗时', '模型']}
                        empty="暂无线上会话"
                        minWidth="920px"
                        rows={sessions.map((item) => [
                            <div className="grid gap-2">
                                <button className="text-left underline-offset-2 hover:underline" onClick={() => onOpenSession(item.session_id)}>
                                    <span className="block max-w-[260px] truncate font-black text-sky-700">{sessionTitle(item)}</span>
                                    <span className="block max-w-[260px] truncate font-mono text-[11px] font-semibold text-slate-400">{item.session_id}</span>
                                </button>
                                <Button tone="light" onClick={() => openOriginalSession(item)}>原始会话</Button>
                            </div>,
                            sceneLabel(item.scene),
                            <Badge tone={item.status === 'completed' ? 'good' : 'bad'}>{item.status === 'completed' ? '已完成' : '异常'}</Badge>,
                            <Badge tone={item.environment_failure ? 'warn' : failureInfo(failureKey(item)).tone}>{failureInfo(failureKey(item)).title}</Badge>,
                            <Badge tone={scoreTone(item.latest_score)}>{fmtPct(item.latest_score)}</Badge>,
                            item.turn_count || 0,
                            fmtMs(item.latency_ms),
                            item.model || 'n/a',
                        ])}
                    />
                )}
                detail={!vm ? (
                    <ExplainEmpty title="选择左侧一段会话" body="这里会用普通话解释这段会话是否完成、Agent 做了哪些步骤、是否需要人工处理。" />
                ) : (
                    <div className="grid gap-4">
                        <ConclusionCard
                            title={`${vm.title}：${vm.conclusion}`}
                            body={`${vm.reason}。${vm.nextAction}`}
                            tone={scoreTone(vm.quality)}
                            action={<Button tone="light" onClick={() => openOriginalSession(selected)}>打开原始会话</Button>}
                        />
                        <ScoreBreakdown detail={latestTurn || selected} />
                        <div className="grid grid-cols-2 gap-2">
                            <MiniFact label="场景" value={vm.scene} />
                            <MiniFact label="执行器" value={vm.worker} />
                            <MiniFact label="轮次" value={vm.turnCount} />
                            <MiniFact label="耗时" value={fmtMs(vm.latency)} />
                            <MiniFact label="工具调用" value={vm.tools.length} />
                            <MiniFact label="成本" value={fmtMoney(vm.totalCost)} />
                        </div>
                        <div>
                            <SectionTitle title="每轮执行" hint="一段 session 可以包含多轮用户消息，每轮都会生成独立执行轨迹。" />
                            <div className="grid gap-2">
                                {vm.turns.map((turn: any, index: number) => {
                                    const run = turn.run || {};
                                    const traceId = run.trace_id || run.id;
                                    const tools = Array.isArray(turn.tool_calls) ? turn.tool_calls : [];
                                    return (
                                        <div key={run.id || index} className="rounded-lg border border-slate-200 bg-white p-3">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge tone="info">第 {index + 1} 轮</Badge>
                                                <Badge tone={run.status === 'completed' ? 'good' : 'bad'}>{run.status === 'completed' ? '完成' : '异常'}</Badge>
                                                {failureKey(run) !== 'none' && <Badge tone={run.environment_failure ? 'warn' : failureInfo(failureKey(run)).tone}>{failureInfo(failureKey(run)).title}</Badge>}
                                                <Badge tone={scoreTone(run.overall_quality)}>{fmtPct(run.overall_quality)}</Badge>
                                                <span className="ml-auto text-[12px] font-semibold text-slate-500">{fmtMs(run.latency_ms)}</span>
                                            </div>
                                            <div className="mt-2 text-[13px] font-semibold text-slate-600">
                                                {sceneLabel(run.scene)} · {modelLabel(run)} · 工具 {tools.length} 个
                                            </div>
                                            <div className="mt-3 flex flex-wrap gap-2">
                                                <Button tone="light" onClick={() => onOpenTrace(traceId)}>查看执行过程</Button>
                                                <Button tone="light" onClick={() => openOriginalSession(run)}>原始会话</Button>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                        {vm.tools.length > 0 && (
                            <div>
                                <SectionTitle title="最近一轮工具调用" hint="外部工具是否成功、耗时多少、失败时影响是什么。" />
                                <ToolList tools={vm.tools} />
                            </div>
                        )}
                        {expertMode && <DeveloperDetails value={selected} />}
                    </div>
                )}
            />
        </div>
    );
}

function ExecutionProcess({
    traces,
    selected,
    expertMode,
    onOpenTrace,
    onAddToDataset,
    onSendReview,
}: {
    traces: any[];
    selected: any;
    expertMode: boolean;
    onOpenTrace: (traceId: string) => void;
    onAddToDataset: (traceId: string) => Promise<void>;
    onSendReview: (runId: string) => Promise<void>;
}) {
    const run = selected?.run || {};
    const timeline = buildExecutionTimelineView(selected);
    const traceId = run.trace_id || run.id;
    return (
        <div className="grid gap-5">
            <PageIntro
                title="按 Trace 还原 Agent 每一步"
                body="左侧选择一次执行，右侧立即展示结论、时间线、工具调用和原始会话入口。排障时不用在长页面里来回跳。"
            />
            <WorkspaceSplit
                listTitle="执行轨迹列表"
                listHint="选择一条 Trace 后，右侧时间线会解释 Agent 的每一步。"
                detailTitle="执行过程解读"
                detailHint={selected ? `${sceneLabel(run.scene)} / ${fmtMs(run.latency_ms)}` : '等待选择 Trace'}
                detailAction={selected && (
                    <div className="flex gap-2">
                        <Button tone="light" onClick={() => onAddToDataset(traceId)}>加入数据集</Button>
                        <Button tone="light" onClick={() => onSendReview(run.id)}>送人工审核</Button>
                        <Button tone="light" onClick={() => openOriginalSession(run)}>原始会话</Button>
                    </div>
                )}
                list={(
                    <SimpleTable
                        headers={['Trace 执行轨迹', '会话', '场景', '结果', '根因', '质量', '步骤', '耗时']}
                        empty="暂无执行轨迹"
                        minWidth="940px"
                        rows={traces.map((item) => [
                            <button className="font-mono text-sky-700 underline-offset-2 hover:underline" onClick={() => onOpenTrace(item.trace_id || item.id)}>{item.trace_id || item.id}</button>,
                            <span>
                                <span className="block max-w-[220px] truncate font-black text-slate-800">{sessionTitle(item)}</span>
                                <span className="block max-w-[220px] truncate font-mono text-[11px] font-semibold text-slate-400">{item.session_id}</span>
                                <span className="mt-2 block">
                                    <Button tone="light" onClick={() => openOriginalSession(item)}>原始会话</Button>
                                </span>
                            </span>,
                            sceneLabel(item.scene),
                            <Badge tone={item.status === 'completed' ? 'good' : 'bad'}>{item.status === 'completed' ? '完成' : '异常'}</Badge>,
                            <Badge tone={item.environment_failure ? 'warn' : failureInfo(failureKey(item)).tone}>{failureInfo(failureKey(item)).title}</Badge>,
                            <Badge tone={scoreTone(item.score)}>{fmtPct(item.score)}</Badge>,
                            item.span_count ?? 0,
                            fmtMs(item.latency_ms),
                        ])}
                    />
                )}
                detail={(
                    !selected ? (
                        <ExplainEmpty title="选择一条 Trace 执行轨迹" body="这里会按时间线解释 Agent 先理解了什么、调用了什么工具、在哪里失败或完成。" />
                    ) : (
                        <div className="grid gap-4">
                            <ConclusionCard
                                title={run.environment_failure ? '环境或模型服务导致失败' : run.agent_fallback ? 'Agent 兜底需要复核' : run.status === 'completed' && Number(run.overall_quality || 0) >= 0.8 ? '这次执行完成了任务' : '这次执行需要复核'}
                                body={`${sessionTitle(run)} / ${sceneLabel(run.scene)} / ${run.worker || run.agent_id || '未知执行器'} / ${modelLabel(run)} / ${failureInfo(failureKey(run)).title} / ${fmtMs(run.latency_ms)}`}
                                tone={run.environment_failure ? 'warn' : scoreTone(run.overall_quality)}
                            />
                            <ScoreBreakdown detail={selected} />
                            <Timeline items={timeline} />
                            {selected.tool_calls?.length > 0 && (
                                <div>
                                    <SectionTitle title="工具调用结果" hint="工具失败通常是影响回答质量的第一优先排查点。" />
                                    <ToolList tools={selected.tool_calls} />
                                </div>
                            )}
                            {expertMode && <DeveloperDetails value={selected} />}
                        </div>
                    )
                )}
            />
        </div>
    );
}

function FailureInsights({ failures, traces, onOpenTrace }: { failures: any; traces: any[]; onOpenTrace: (traceId: string) => void }) {
    const insights = buildFailureInsightView(failures);
    return (
        <div className="grid gap-5">
            <PageIntro
                title="先看问题类型，再回到具体 Trace"
                body="每个异常都应该能落到模型服务、工具接口、Agent 判断、回答质量、安全风险或评测框架本身。"
            />
            <Panel title="问题归因">
                <FailureBars insights={insights} emptyText="当前窗口内没有明确失败归因。" />
            </Panel>
            <Panel title="低分或异常执行">
                <div className="grid gap-2 p-4">
                    {traces.filter((item) => item.status !== 'completed' || item.environment_failure || item.agent_fallback || Number(item.score || item.overall_quality || 1) < 0.8).slice(0, 8).map((item) => (
                        <div key={item.id || item.trace_id} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-left hover:bg-white">
                            <button onClick={() => onOpenTrace(item.trace_id || item.id)} className="w-full text-left">
                            <div className="flex items-center justify-between gap-2">
                                <span className="truncate font-mono text-[12px] font-black text-sky-700">{item.trace_id || item.id}</span>
                                <Badge tone={scoreTone(item.score || item.overall_quality)}>{fmtPct(item.score || item.overall_quality)}</Badge>
                            </div>
                            <div className="mt-2 text-[12px] font-semibold text-slate-600">{sceneLabel(item.scene)} · {item.status || 'unknown'} · {failureInfo(failureKey(item)).title} · {fmtMs(item.latency_ms)}</div>
                            </button>
                            <div className="mt-3">
                                <Button tone="light" onClick={() => openOriginalSession(item)}>打开原始会话</Button>
                            </div>
                        </div>
                    ))}
                    {!traces.length && <ExplainEmpty title="暂无 Trace" body="完成线上对话采集后，这里会列出需要复核的低分执行。" />}
                </div>
            </Panel>
        </div>
    );
}

function CostPerformance({ data }: { data: ReturnType<typeof buildCostPerformanceView> }) {
    return (
        <div className="grid gap-4">
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-6">
                <MetricCard title="运行数" value={data.totalRuns} />
                <MetricCard title="P50 耗时" value={fmtMs(data.latencyP50)} />
                <MetricCard title="P95 耗时" value={fmtMs(data.latencyP95)} tone={Number(data.latencyP95) > 10000 ? 'warn' : 'neutral'} />
                <MetricCard title="输入 Token" value={data.inputTokens} />
                <MetricCard title="输出 Token" value={data.outputTokens} />
                <MetricCard title="总成本" value={fmtMoney(data.totalCost)} />
            </div>
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-3">
                <MetricCard title="缓存命中" value={data.cachedTokens} tone="good" />
                <MetricCard title="缓存未命中" value={data.cacheMissTokens} tone={Number(data.cacheMissTokens) > Number(data.cachedTokens) ? 'warn' : 'neutral'} />
                <MetricCard title="缓存命中率" value={fmtPct(data.cacheHitRate)} tone={Number(data.cacheHitRate) < 0.5 ? 'warn' : 'good'} />
            </div>
            <Panel title="成本来源">
                <div className="grid gap-3 p-4 md:grid-cols-3">
                    <ConclusionCard title="模型 Token 成本" body={fmtMoney(data.tokenCost)} tone="info" />
                    <ConclusionCard title="外部工具成本" body={fmtMoney(data.toolCost)} tone="info" />
                    <ConclusionCard title="总成本" body={fmtMoney(data.totalCost)} tone="neutral" />
                </div>
                <DistributionMap title="按模型/Provider 分布" values={data.byProvider} />
            </Panel>
        </div>
    );
}

function SafetyGovernance({ data }: { data: ReturnType<typeof buildSafetyGovernanceView> }) {
    return (
        <div className="grid gap-4">
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-5">
                <MetricCard title="运行数" value={data.totalRuns} />
                <MetricCard title="无泄露" value={fmtPct(data.noLeak)} tone={scoreTone(data.noLeak)} />
                <MetricCard title="敏感泄露" value={fmtPct(data.secretLeakRate)} tone={Number(data.secretLeakRate) > 0 ? 'bad' : 'good'} />
                <MetricCard title="策略违规" value={fmtPct(data.policyViolationRate)} tone={Number(data.policyViolationRate) > 0 ? 'bad' : 'good'} />
                <MetricCard title="人工升级" value={fmtPct(data.humanEscalationRate)} tone={Number(data.humanEscalationRate) > 0 ? 'warn' : 'neutral'} />
            </div>
            <Panel title="安全治理说明">
                <div className="grid gap-3 p-4 md:grid-cols-2">
                    <ConclusionCard title="优先级规则" body="任何敏感泄露或策略违规都应优先进入人工审核，不依赖平均分。" tone="bad" />
                    <ConclusionCard title="当前处理建议" body={Number(data.secretLeakRate || 0) > 0 || Number(data.policyViolationRate || 0) > 0 ? '立即查看安全风险会话并复核输出。' : '当前窗口未发现明显安全风险。'} tone={Number(data.secretLeakRate || 0) > 0 || Number(data.policyViolationRate || 0) > 0 ? 'warn' : 'good'} />
                </div>
            </Panel>
        </div>
    );
}

function HumanReviews({ data, onRefresh, onOpenTrace }: { data: any; onRefresh: () => void; onOpenTrace: (traceId: string) => void }) {
    const records = data?.records || [];
    return (
        <Panel title="待人工审核">
            <div className="grid gap-3 p-4">
                {records.map((item: any) => {
                    const run = item.run || {};
                    const review = item.review || {};
                    return (
                        <div key={run.id} className="rounded-lg border border-slate-200 bg-white p-4">
                            <div className="flex flex-wrap items-center gap-2">
                                <Badge tone="warn">待处理</Badge>
                                <span className="font-mono text-[12px] font-black text-sky-700">{run.id}</span>
                                <span className="ml-auto text-[12px] font-semibold text-slate-500">{sceneLabel(run.scene)}</span>
                            </div>
                            <div className="mt-2 text-sm font-semibold text-slate-700">
                                进入审核原因：{displayValue(review.failure_reason || review.reason || run.failure_class, '低分或系统规则命中')}
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                                <Button tone="green" onClick={async () => { await appApi.evaluations.submitMonitoringReview(run.id, { decision: 'accepted' }); await onRefresh(); }}>接受</Button>
                                <Button tone="danger" onClick={async () => { await appApi.evaluations.submitMonitoringReview(run.id, { decision: 'rejected', reason: 'manual_reject' }); await onRefresh(); }}>拒绝</Button>
                                <Button tone="light" onClick={async () => { await appApi.evaluations.submitMonitoringReview(run.id, { decision: 'converted_to_case', reason: 'converted_to_case' }); await onRefresh(); }}>转数据集 Case</Button>
                                <Button tone="light" onClick={() => onOpenTrace(run.trace_id || run.id)}>查看执行过程</Button>
                                <Button tone="light" onClick={() => openOriginalSession(run)}>打开原始会话</Button>
                            </div>
                        </div>
                    );
                })}
                {!records.length && <ExplainEmpty title="暂无待审核会话" body="低分、工具失败、安全风险或人工标记的会话会出现在这里。" />}
            </div>
        </Panel>
    );
}

function OfflineEvaluation({ reports, jobs, datasets, onRefresh }: { reports: any[]; jobs: any[]; datasets: any[]; onRefresh: () => Promise<void> | void }) {
    const [runner, setRunner] = useState('fixture');
    const [suite, setSuite] = useState('quick');
    const [numTrials, setNumTrials] = useState(1);
    const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:8000');
    const [includeJudge, setIncludeJudge] = useState(false);
    const [outcomeVerify, setOutcomeVerify] = useState(false);
    const [persistDb, setPersistDb] = useState(true);
    const [requireDbPersist, setRequireDbPersist] = useState(false);
    const [busy, setBusy] = useState(false);
    const [localMessage, setLocalMessage] = useState('');
    const [selectedReport, setSelectedReport] = useState(reports[0]?.name || '');
    const [reportDetail, setReportDetail] = useState<any>(null);
    const [selectedCase, setSelectedCase] = useState<any>(null);
    const [selectedDataset, setSelectedDataset] = useState(datasets[0]?.name || datasets[0]?.suite || 'quick');
    const [datasetDetail, setDatasetDetail] = useState<any>(null);
    const [activeJobDetail, setActiveJobDetail] = useState<any>(null);
    const [offlineTab, setOfflineTab] = useState<'run' | 'jobs' | 'datasets' | 'reports'>('run');
    const activeJob = jobs.find((item) => ['queued', 'running'].includes(item.status));

    useEffect(() => {
        if (!selectedReport && reports[0]?.name) setSelectedReport(reports[0].name);
    }, [reports, selectedReport]);

    useEffect(() => {
        if (!selectedDataset && datasets[0]) setSelectedDataset(datasets[0].name || datasets[0].suite);
    }, [datasets, selectedDataset]);

    useEffect(() => {
        if (!selectedReport) return;
        let cancelled = false;
        appApi.evaluations.getReport(selectedReport)
            .then((data) => {
                if (cancelled) return;
                setReportDetail(data.report || null);
                setSelectedCase(null);
            })
            .catch((error) => {
                if (!cancelled) setLocalMessage(getErrorMessage(error));
            });
        return () => { cancelled = true; };
    }, [selectedReport]);

    useEffect(() => {
        if (!selectedDataset) return;
        let cancelled = false;
        appApi.evaluations.listDatasetCases(selectedDataset)
            .then((data) => {
                if (!cancelled) setDatasetDetail(data);
            })
            .catch(async () => {
                try {
                    const data = await appApi.evaluations.listHubDatasetCases(selectedDataset);
                    if (!cancelled) setDatasetDetail(data);
                } catch {
                    if (!cancelled) setDatasetDetail(null);
                }
            });
        return () => { cancelled = true; };
    }, [selectedDataset]);

    useEffect(() => {
        if (!activeJob) {
            setActiveJobDetail(null);
            return;
        }
        const timer = window.setInterval(() => {
            void appApi.evaluations.getEvalJob(activeJob.id).then((data) => {
                setActiveJobDetail(data.job || null);
                if (!['queued', 'running'].includes(data.job?.status)) {
                    void onRefresh();
                }
            }).catch(() => {});
        }, 2000);
        return () => window.clearInterval(timer);
    }, [activeJob?.id, onRefresh]);

    const runEval = async () => {
        const risky = runner === 'live' || suite !== 'quick' || includeJudge || outcomeVerify;
        if (risky && !window.confirm('这次评测可能调用真实模型、外部工具或 LLM Judge，确认开始吗？')) return;
        setBusy(true);
        setLocalMessage('');
        try {
            await appApi.evaluations.createEvalJob({
                runner,
                suite,
                num_trials: numTrials,
                base_url: baseUrl,
                include_llm_judge: includeJudge,
                outcome_verify: outcomeVerify,
                persist_db: persistDb,
                require_db_persist: requireDbPersist,
            });
            setLocalMessage('评测任务已创建');
            await onRefresh();
        } catch (error) {
            const apiError = error as ApiError;
            const active = (apiError as any)?.detail?.active_job;
            setLocalMessage(active ? `已有任务运行中：${active.id}` : getErrorMessage(error));
        } finally {
            setBusy(false);
        }
    };

    const cancelJob = async (jobId: string) => {
        setBusy(true);
        try {
            await appApi.evaluations.cancelEvalJob(jobId);
            setLocalMessage('任务已取消');
            await onRefresh();
        } catch (error) {
            setLocalMessage(getErrorMessage(error));
        } finally {
            setBusy(false);
        }
    };

    const openCase = async (caseId: string) => {
        if (!selectedReport) return;
        try {
            const detail = await appApi.evaluations.getCaseDetail(selectedReport, caseId);
            setSelectedCase(detail);
        } catch (error) {
            setLocalMessage(getErrorMessage(error));
        }
    };

    const cases = Array.isArray(reportDetail?.results) ? reportDetail.results : [];
    const datasetCases = Array.isArray(datasetDetail?.cases) ? datasetDetail.cases : [];

    return (
        <div className="grid gap-4">
            {localMessage && <Badge tone={localMessage.includes('失败') || localMessage.includes('已有') ? 'warn' : 'good'}>{localMessage}</Badge>}
            <PageIntro
                title="发布前回归检查台"
                body="这里可以运行 fixture quick、查看后台任务、检查评测集覆盖和报告结果。日常人工验证优先从 fixture quick 开始。"
            />
            <div className="flex gap-2 overflow-x-auto rounded-lg border border-slate-200 bg-white p-2">
                {[
                    ['run', '运行评测'],
                    ['jobs', '任务队列'],
                    ['datasets', '评测集'],
                    ['reports', '报告结果'],
                ].map(([key, label]) => (
                    <button
                        key={key}
                        onClick={() => setOfflineTab(key as typeof offlineTab)}
                        className={`whitespace-nowrap rounded-md px-4 py-2 text-[13px] font-black transition ${offlineTab === key ? 'bg-slate-950 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {offlineTab === 'run' && (
                <Panel title="运行评测">
                    <div className="grid gap-4 p-4">
                        <div className="grid gap-3 md:grid-cols-4">
                            <label className="grid gap-1 text-[12px] font-black text-slate-500">Runner<Select value={runner} onChange={(e) => setRunner(e.target.value)}><option value="fixture">fixture</option><option value="live">live</option></Select></label>
                            <label className="grid gap-1 text-[12px] font-black text-slate-500">Suite<Select value={suite} onChange={(e) => setSuite(e.target.value)}><option value="quick">quick</option><option value="full">full</option><option value="live-smoke">live-smoke</option></Select></label>
                            <label className="grid gap-1 text-[12px] font-black text-slate-500">Trials<Input value={String(numTrials)} onChange={(e) => setNumTrials(Math.max(1, Number(e.target.value) || 1))} /></label>
                            {runner === 'live' && <label className="grid gap-1 text-[12px] font-black text-slate-500">Base URL<Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>}
                        </div>
                        <div className="grid gap-2 text-[13px] font-semibold text-slate-600 md:grid-cols-4">
                            <Toggle checked={includeJudge} onChange={setIncludeJudge} label="启用 LLM Judge" />
                            <Toggle checked={outcomeVerify} onChange={setOutcomeVerify} label="启用 Outcome Verify" />
                            <Toggle checked={persistDb} onChange={setPersistDb} label="写入评测数据库" />
                            <Toggle checked={requireDbPersist} onChange={setRequireDbPersist} label="DB 写失败则任务失败" />
                        </div>
                        <div className="flex flex-wrap items-center gap-3">
                            <Button tone="dark" onClick={runEval} disabled={busy || Boolean(activeJob)}>{activeJob ? `任务运行中：${activeJob.id.slice(0, 8)}` : '开始运行'}</Button>
                            <span className="text-[12px] font-semibold text-slate-500">fixture quick 会直接运行；live/full/Judge/Outcome 会二次确认。</span>
                        </div>
                    </div>
                </Panel>
            )}

            {offlineTab === 'jobs' && (
                <Panel title="任务队列">
                    <SimpleTable
                        headers={['任务', '状态', '范围', '报告', '时间', '操作']}
                        empty="暂无评测任务"
                        rows={jobs.map((item) => [
                            <span className="font-mono text-sky-700">{item.id}</span>,
                            <Badge tone={item.status === 'succeeded' ? 'good' : item.status === 'failed' ? 'bad' : ['queued', 'running'].includes(item.status) ? 'warn' : 'neutral'}>{item.status}</Badge>,
                            `${item.runner || 'n/a'} / ${item.suite || 'n/a'}`,
                            item.report_name ? <button className="font-mono text-sky-700 underline" onClick={() => setSelectedReport(item.report_name)}>{item.report_name}</button> : 'n/a',
                            `${fmtTime(item.started_at || item.created_at)} -> ${fmtTime(item.finished_at)}`,
                            ['queued', 'running'].includes(item.status) ? <Button tone="danger" onClick={() => cancelJob(item.id)}>取消</Button> : <Button tone="light" onClick={() => item.report_name && setSelectedReport(item.report_name)}>打开报告</Button>,
                        ])}
                    />
                    {activeJobDetail?.logs_tail && (
                        <div className="border-t border-slate-100 p-4">
                            <SectionTitle title="运行日志" hint="后台 run_eval.py 的日志尾部。" />
                            <pre className="max-h-56 overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] font-semibold text-slate-100">{activeJobDetail.logs_tail}</pre>
                        </div>
                    )}
                </Panel>
            )}

            {offlineTab === 'datasets' && (
                <Panel title="数据集">
                    <div className="grid gap-3 p-4">
                        <Select value={selectedDataset} onChange={(e) => setSelectedDataset(e.target.value)}>
                            {datasets.map((item) => {
                                const name = item.name || item.suite;
                                return <option key={name} value={name}>{name} / {item.version || 'file'}</option>;
                            })}
                        </Select>
                        <div className="grid grid-cols-3 gap-2">
                            <MiniFact label="Case 数" value={datasetDetail?.total_cases ?? datasetCases.length} />
                            <MiniFact label="版本" value={datasets.find((item) => (item.name || item.suite) === selectedDataset)?.version || 'n/a'} />
                            <MiniFact label="状态" value={datasets.find((item) => (item.name || item.suite) === selectedDataset)?.status || 'active'} />
                        </div>
                        <ExplainEmpty
                            title={`${selectedDataset || 'quick'} 评测集`}
                            body="这里展示发布前回归用例：输入任务、期望路由/工具、评分指标和优先级。quick 用于 PR 或日常快速检查，full/live-smoke 用于定时或人工验证。"
                        />
                    </div>
                    <DistributionMap title="Scene 分布" values={datasets.find((item) => (item.name || item.suite) === selectedDataset)?.by_scene || {}} />
                    <DistributionMap title="Category 分布" values={datasets.find((item) => (item.name || item.suite) === selectedDataset)?.by_category || {}} />
                    <DistributionMap title="Priority 分布" values={datasets.find((item) => (item.name || item.suite) === selectedDataset)?.by_priority || {}} />
                    <SimpleTable
                        headers={['Case', '任务内容', '场景/类别', '期望', '评分指标', '优先级']}
                        minWidth="1280px"
                        empty="暂无数据集 Case"
                        rows={datasetCases.slice(0, 12).map((item: any) => [
                            <span className="font-mono text-sky-700">{item.case_id || item.id}</span>,
                            <span className="block max-w-[260px] whitespace-normal leading-relaxed">{item.task || item.input || item.user_message || '未记录任务内容'}</span>,
                            <span>{sceneLabel(item.scene)}<span className="block text-[11px] text-slate-400">{item.category || 'n/a'}</span></span>,
                            <DatasetExpectationSummary value={item.expectations_summary || item.expectations || item.expected} />,
                            <DatasetScoringSummary value={item.scoring_summary || item.scoring || item.weights} />,
                            <Badge tone={item.priority === 'p0' ? 'bad' : 'info'}>{item.priority || 'p1'}</Badge>,
                        ])}
                    />
                </Panel>
            )}

            {offlineTab === 'reports' && (
                <WorkspaceSplit
                    listTitle="报告结果"
                    listHint="选择报告和 Case 后，右侧立即展示评分、Trial、Trace Timeline。"
                    detailTitle="Case 详情"
                    detailHint={selectedCase?.case?.case_id || '等待选择 Case'}
                    list={(
                        <div className="grid gap-3">
                            <div className="grid gap-3 p-4">
                                <Select value={selectedReport} onChange={(e) => setSelectedReport(e.target.value)}>
                                    {reports.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
                                </Select>
                                {reportDetail ? (
                                    <>
                                        <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
                                            <MetricCard title="通过率" value={fmtPct(reportDetail.overall_success_rate)} tone={scoreTone(reportDetail.overall_success_rate)} />
                                            <MetricCard title="Case" value={reportDetail.total_cases || cases.length} />
                                            <MetricCard title="Trial" value={reportDetail.total_trials || 0} />
                                            <MetricCard title="失败" value={reportDetail.failed_cases || 0} tone={reportDetail.failed_cases ? 'warn' : 'good'} />
                                            <MetricCard title="耗时" value={`${Number(reportDetail.duration_seconds || 0).toFixed(2)}s`} />
                                        </div>
                                        {reportDetail.stability && (
                                            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                                                <MetricCard title={`pass@${reportDetail.stability.k || 1}`} value={fmtPct(reportDetail.stability.pass_at_k)} tone={scoreTone(reportDetail.stability.pass_at_k)} hint="多次 trial 至少一次通过" />
                                                <MetricCard title={`pass^${reportDetail.stability.k || 1}`} value={fmtPct(reportDetail.stability.pass_all_k)} tone={scoreTone(reportDetail.stability.pass_all_k)} hint="多次 trial 全部通过" />
                                                <MetricCard title="分数波动" value={Number(reportDetail.stability.trial_variance || 0).toFixed(4)} tone={Number(reportDetail.stability.trial_variance || 0) > 0.05 ? 'warn' : 'good'} />
                                                <MetricCard title="不稳定 Case" value={reportDetail.stability.flaky_cases?.length || 0} tone={reportDetail.stability.flaky_cases?.length ? 'warn' : 'good'} />
                                            </div>
                                        )}
                                    </>
                                ) : <ExplainEmpty title="暂无报告详情" body="选择报告或运行一次 fixture quick 后，这里会展示详细结果。" />}
                            </div>
                            {reportDetail?.stability?.flaky_cases?.length > 0 && (
                                <SimpleTable
                                    headers={['不稳定 Case', '通过次数', 'Trial 数', '分数波动', '分数序列']}
                                    empty="暂无不稳定 Case"
                                    rows={reportDetail.stability.flaky_cases.map((item: any) => [
                                        <span className="font-mono text-amber-700">{item.case_id}</span>,
                                        item.pass_count,
                                        item.trials,
                                        Number(item.variance || 0).toFixed(4),
                                        displayValue(item.scores),
                                    ])}
                                />
                            )}
                            <SimpleTable
                                headers={['Case', '场景', '类别', '优先级', '通过率', '操作']}
                                empty="暂无 Case 结果"
                                minWidth="720px"
                                rows={cases.map((item: any) => [
                                    <span className="font-mono text-sky-700">{item.case_id}</span>,
                                    sceneLabel(item.scene),
                                    item.category || 'n/a',
                                    <Badge tone={item.priority === 'p0' ? 'bad' : 'info'}>{item.priority || 'p1'}</Badge>,
                                    <Badge tone={scoreTone(item.success_rate)}>{fmtPct(item.success_rate)}</Badge>,
                                    <Button tone="light" onClick={() => openCase(item.case_id)}>详情</Button>,
                                ])}
                            />
                        </div>
                    )}
                    detail={selectedCase ? <OfflineCaseInspector detail={selectedCase} /> : <ExplainEmpty title="选择一个 Case" body="这里会展示任务、期望、评分明细、Trial 失败原因和离线 Trace Timeline。" />}
                />
            )}
        </div>
    );
}

function DataFeedbackLoop({
    datasets,
    cases,
    experiments,
    evaluators,
    componentRuns,
    simulationScenarios,
    judgeAgreement,
    onRefresh,
}: {
    datasets: any[];
    cases: any[];
    experiments: any[];
    evaluators: any[];
    componentRuns: any[];
    simulationScenarios: any[];
    judgeAgreement: any;
    onRefresh: () => Promise<void> | void;
}) {
    const [message, setMessage] = useState('');
    const [busy, setBusy] = useState(false);

    const generateDraftCase = async () => {
        setBusy(true);
        setMessage('');
        try {
            await appApi.evaluations.generateDatasetCases('regression', {
                source: 'manual',
                version: 'draft',
                task: '用户需要在预算、口味和距离约束下得到可执行的餐饮建议',
                scene: 'eat_out',
                category: 'regression',
                priority: 'p1',
                must_include: ['推荐理由', '下一步行动'],
                notes: '从 Web 工作台生成的 draft case，需要人工审核后进入 active。',
            });
            setMessage('已生成 regression draft case，等待人工审核。');
            await onRefresh();
        } catch (error) {
            setMessage(getErrorMessage(error));
        } finally {
            setBusy(false);
        }
    };

    const runComponent = async (component: string) => {
        setBusy(true);
        setMessage('');
        try {
            await appApi.evaluations.createComponentRun({ component, dataset: 'component-regression' });
            setMessage(`${component} 组件评测已生成。`);
            await onRefresh();
        } catch (error) {
            setMessage(getErrorMessage(error));
        } finally {
            setBusy(false);
        }
    };

    const createSimulationScenario = async (runner: 'deterministic' | 'live_agent' = 'deterministic') => {
        if (runner === 'live_agent' && !window.confirm('真实 Agent 模拟会调用模型和工具，并产生线上监控 Trace，确认运行吗？')) return;
        setBusy(true);
        setMessage('');
        try {
            const response = await appApi.evaluations.createHubSimulationScenario({
                name: '餐饮多轮目标达成 smoke',
                description: 'Synthetic User 用多轮追问验证 Agent 是否能持续推进用户目标。',
                status: 'draft',
                scenario: {
                    persona: '预算敏感、需要明确下一步的用户',
                    goal: '找到人均 100 以内、离静安寺近、适合今晚聚餐的选择',
                    scene: 'eat_out',
                    max_turns: 5,
                    success_criteria: ['给出餐厅建议', '解释预算匹配', '给出可执行下一步'],
                },
            });
            const scenarioId = response?.scenario?.id;
            if (scenarioId) {
                await appApi.evaluations.createHubSimulationRun(scenarioId, { runner, max_turns: 5 });
            }
            setMessage(runner === 'live_agent' ? '真实 Agent Synthetic User 模拟已创建。' : 'Synthetic User 场景和一次模拟运行已创建。');
            await onRefresh();
        } catch (error) {
            setMessage(getErrorMessage(error));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="grid gap-4">
            {message && <Badge tone={message.includes('失败') ? 'bad' : 'info'}>{message}</Badge>}
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <MetricCard title="Dataset 数据集" value={datasets.length} />
                <MetricCard title="Regression Case" value={cases.length} />
                <MetricCard title="Experiment 实验" value={experiments.length} />
                <MetricCard title="组件评测" value={componentRuns.length} />
            </div>
            <Panel title="闭环路径">
                <div className="grid gap-3 p-4 md:grid-cols-5">
                    {['线上会话', '人工审核', '转数据集', '离线实验', '发布门禁'].map((item, index) => (
                        <div key={item} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                            <div className="text-[11px] font-black text-slate-400">STEP {index + 1}</div>
                            <div className="mt-1 text-sm font-black text-slate-900">{item}</div>
                        </div>
                    ))}
                </div>
            </Panel>
            <div className="grid gap-4 xl:grid-cols-3">
                <Panel title="评测数据生成器">
                    <div className="grid gap-3 p-4">
                        <ExplainEmpty title="生成结果默认进入 draft" body="可以从人工输入、Trace、失败报告或文档目标生成候选 Case；审核通过后才进入 active 数据集。" />
                        <Button tone="dark" onClick={generateDraftCase} disabled={busy}>生成示例 draft case</Button>
                    </div>
                </Panel>
                <Panel title="Synthetic User 多轮评测">
                    <div className="grid gap-3 p-4">
                        <MiniFact label="场景数" value={simulationScenarios.length} />
                        <Button tone="light" onClick={() => createSimulationScenario('deterministic')} disabled={busy}>创建并运行 smoke 场景</Button>
                        <Button tone="dark" onClick={() => createSimulationScenario('live_agent')} disabled={busy}>运行真实 Agent 多轮模拟</Button>
                    </div>
                </Panel>
                <Panel title="Judge 校准">
                    <div className="grid gap-3 p-4">
                        <MiniFact label="Agreement" value={fmtPct(judgeAgreement?.agreement_rate || 0)} />
                        <MiniFact label="False Positive" value={fmtPct(judgeAgreement?.false_positive_rate || 0)} />
                        <MiniFact label="False Negative" value={fmtPct(judgeAgreement?.false_negative_rate || 0)} />
                    </div>
                </Panel>
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
                <Panel title="数据集">
                    <SimpleTable
                        headers={['名称', '版本', '状态', 'Case 数', '来源覆盖']}
                        empty="暂无数据集"
                        rows={datasets.map((item) => [
                            item.name || item.suite || 'n/a',
                            item.version || 'n/a',
                            <Badge tone="info">{item.status || 'active'}</Badge>,
                            item.total_cases || 0,
                            displayValue(item.by_source || {}),
                        ])}
                    />
                </Panel>
                <Panel title="实验与评测器">
                    <SimpleTable
                        headers={['对象', '类型', '状态', 'Owner']}
                        empty="暂无实验或评测器"
                        rows={[
                            ...experiments.map((item) => [item.name, 'Experiment 实验', <Badge tone="info">{item.status || 'draft'}</Badge>, item.owner || 'n/a']),
                            ...evaluators.map((item) => [item.name, `Evaluator / ${item.type}`, <Badge tone={item.status === 'active' ? 'good' : 'neutral'}>{item.status}</Badge>, item.owner || 'n/a']),
                        ]}
                    />
                </Panel>
            </div>
            <Panel title="组件级评测">
                <div className="flex flex-wrap gap-2 p-4">
                    {['router', 'tool', 'rag', 'schema', 'llm'].map((component) => (
                        <Button key={component} tone="light" onClick={() => runComponent(component)} disabled={busy}>运行 {component}</Button>
                    ))}
                </div>
                <SimpleTable
                    headers={['组件', '报告', '通过率', '时间']}
                    empty="暂无组件评测"
                    rows={componentRuns.map((item) => [
                        item.component || item.suite || 'component',
                        <span className="font-mono text-sky-700">{item.report_name}</span>,
                        <Badge tone={scoreTone(item.success_rate)}>{fmtPct(item.success_rate)}</Badge>,
                        fmtTime(item.timestamp),
                    ])}
                />
            </Panel>
            <Panel title="Synthetic User 场景">
                <SimpleTable
                    headers={['场景', '状态', '目标', '成功标准']}
                    empty="暂无模拟场景"
                    rows={simulationScenarios.map((item) => [
                        item.name,
                        <Badge tone={item.status === 'active' ? 'good' : 'info'}>{item.status}</Badge>,
                        compactValue(item.scenario?.goal || item.description, 80),
                        compactValue(item.scenario?.success_criteria || [], 120),
                    ])}
                />
            </Panel>
        </div>
    );
}

function OfflineCaseDetail({ detail }: { detail: any }) {
    return (
        <Panel title="Case 详情">
            <div className="p-4">
                <OfflineCaseInspector detail={detail} />
            </div>
        </Panel>
    );
}

function OfflineCaseInspector({ detail }: { detail: any }) {
    const caseData = detail.case || {};
    const trials = Array.isArray(detail.trials) ? detail.trials : [];
    const firstTrial = trials[0] || {};
    return (
        <div className="grid gap-4">
            <ConclusionCard
                title={caseData.task || caseData.case_id || '未命名 Case'}
                body={`${sceneLabel(caseData.scene)} / ${caseData.category || 'n/a'} / ${caseData.priority || 'p1'}`}
                tone={scoreTone(caseData.success_rate)}
            />
            <div className="grid grid-cols-2 gap-2">
                <MiniFact label="Case" value={caseData.case_id || caseData.id || 'n/a'} />
                <MiniFact label="优先级" value={caseData.priority || 'p1'} />
                <MiniFact label="类别" value={caseData.category || 'n/a'} />
                <MiniFact label="通过率" value={fmtPct(caseData.success_rate)} />
            </div>
            <ScoreBreakdown detail={{ metrics: caseData.avg_scores || firstTrial.scores || {}, run: { overall_quality: caseData.success_rate } }} />
            <DeveloperDetails value={{ expectations: caseData.expectations || caseData.expected || {}, source: detail.source }} />
            <SimpleTable
                headers={['Trial', '加权分', '失败原因', '缺失指标', '阈值失败']}
                empty="暂无 trial"
                minWidth="680px"
                rows={trials.map((trial: any) => [
                    trial.trial_number ?? 0,
                    <Badge tone={scoreTone(trial.weighted_score)}>{fmtPct(trial.weighted_score)}</Badge>,
                    failureInfo(trial.failure_class).title,
                    Array.isArray(trial.missing_metrics) ? trial.missing_metrics.join(', ') || '无' : '无',
                    Array.isArray(trial.threshold_failures) ? trial.threshold_failures.length : 0,
                ])}
            />
            {firstTrial.trace_timeline?.length > 0 && (
                <div>
                    <SectionTitle title="离线 Trace Timeline" hint="来自评测报告的 trial 轨迹。" />
                    <Timeline items={buildExecutionTimelineView({ events: firstTrial.trace_timeline, run: { latency_ms: firstTrial.duration_ms } })} />
                </div>
            )}
            <DeveloperDetails value={{ case: caseData, trials }} />
        </div>
    );
}

function ExpertConsole({ overview, sessions, traces, selectedTrace, selectedSession, failures, cost, safety }: any) {
    return (
        <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Trace / Span 专家视图">
                <div className="grid gap-3 p-4">
                    <DeveloperDetails value={{ selectedTrace, traces }} defaultOpen />
                </div>
            </Panel>
            <Panel title="Raw Metrics / JSON">
                <div className="grid gap-3 p-4">
                    <DeveloperDetails value={{ overview, sessions, selectedSession, failures, cost, safety }} defaultOpen />
                </div>
            </Panel>
        </div>
    );
}

function Timeline({ items }: { items: ReturnType<typeof buildExecutionTimelineView> }) {
    if (!items.length) return <ExplainEmpty title="还没有可视化执行步骤" body="新采集的会话会自动生成模型调用、工具调用、路由和最终输出步骤。" />;
    return (
        <div className="relative grid gap-3">
            <div className="absolute bottom-4 left-[18px] top-4 w-px bg-slate-200" />
            {items.map((item) => {
                const Icon = item.icon;
                return (
                    <div key={item.id} className="relative grid grid-cols-[38px_minmax(0,1fr)] gap-3">
                        <div className={`z-10 grid h-9 w-9 place-items-center rounded-full border ${toneClass(item.tone)}`}>
                            <Icon size={16} />
                        </div>
                        <div className="rounded-lg border border-slate-200 bg-white p-3">
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="rounded-full bg-slate-950 px-2 py-1 font-mono text-[11px] font-black text-white">STEP {item.index + 1}</span>
                                <span className="font-black text-slate-900">{item.title}</span>
                                <Badge tone={item.tone}>{item.status}</Badge>
                                <span className="ml-auto text-[12px] font-semibold text-slate-500">
                                    {item.duration ? fmtMs(item.duration) : '耗时未记录'}{item.durationEstimated ? ' · 估算' : ''}
                                </span>
                            </div>
                            <div className="mt-1 text-[13px] font-semibold text-slate-600">{item.subtitle}</div>
                            <div className="mt-3 grid gap-2 md:grid-cols-3">
                                <MiniFact label="开始" value={fmtStepTime(item.startedAt)} />
                                <MiniFact label="结束" value={fmtStepTime(item.endedAt)} />
                                <MiniFact label="耗时占比" value={item.durationRatio == null ? '未记录' : `${fmtPct(item.durationRatio)}${item.durationEstimated ? '（估算）' : ''}`} />
                            </div>
                            {(item.inputSummary || item.outputSummary || item.errorSummary) && (
                                <div className="mt-3 grid gap-2">
                                    {item.inputSummary && <StepSummary label="输入" value={item.inputSummary} />}
                                    {item.outputSummary && <StepSummary label="输出" value={item.outputSummary} />}
                                    {item.errorSummary && <StepSummary label="错误" value={item.errorSummary} tone="bad" />}
                                </div>
                            )}
                            <details className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                                <summary className="cursor-pointer text-[12px] font-black text-slate-600">展开参数、结果和 metadata</summary>
                                <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words text-[11px] font-semibold text-slate-600">{JSON.stringify(item.raw, null, 2)}</pre>
                            </details>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

function StepSummary({ label, value, tone = 'neutral' }: { label: string; value: React.ReactNode; tone?: Tone }) {
    return (
        <div className={`rounded-md border px-3 py-2 text-[12px] font-semibold ${tone === 'bad' ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-slate-200 bg-slate-50 text-slate-700'}`}>
            <span className="mr-2 font-black">{label}</span>
            {value}
        </div>
    );
}

function ToolList({ tools }: { tools: any[] }) {
    const summaries = summarizeTools(tools);
    return (
        <div className="grid gap-2">
            {summaries.map((tool) => {
                const avgLatency = tool.latency.length ? tool.latency.reduce((sum, item) => sum + item, 0) / tool.latency.length : null;
                return (
                <div key={tool.name} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={tool.failed ? 'bad' : 'good'}>{tool.failed ? `失败 ${tool.failed}` : '全部成功'}</Badge>
                        <span className="font-black text-slate-900">{toolLabel(tool.name)}</span>
                        <span className="rounded-full bg-white px-2 py-1 text-[12px] font-black text-slate-600">调用 {tool.count} 次</span>
                        <span className="ml-auto text-[12px] font-semibold text-slate-500">{fmtToolMs(avgLatency)}</span>
                    </div>
                    <div className="mt-1 font-mono text-[11px] font-semibold text-slate-400">{tool.name}</div>
                    {tool.errors.length > 0 && <div className="mt-2 text-[12px] font-semibold text-rose-600">{tool.errors.slice(0, 2).join('；')}</div>}
                    {tool.failureClass && <div className="mt-2 text-[12px] font-semibold text-slate-600">{failureInfo(tool.failureClass).title}</div>}
                </div>
                );
            })}
        </div>
    );
}

function ScoreBreakdown({ detail }: { detail: any }) {
    const rows = scoreMetricRows(detail);
    if (!rows.length) {
        return (
            <div>
                <SectionTitle title="评分明细" hint="当前记录还没有写入评分指标；新会话采集后会显示每个评分项。" />
                <ExplainEmpty title="暂无评分明细" body="如果只看到质量百分比，说明这条数据可能来自旧采集链路或评分指标还没有持久化。" />
            </div>
        );
    }
    return (
        <div>
            <SectionTitle title="评分明细" hint="这些分数来自在线轻量评分器，用来解释为什么一段会话被判定为好或需要复核。" />
            <div className="grid gap-2 md:grid-cols-2">
                {rows.map((item) => {
                    const pct = Math.max(0, Math.min(100, Math.round(Number(item.value || 0) * 100)));
                    const dangerMetric = item.key.includes('error') || item.key.includes('fallback') || item.key === 'repeated_action_rate';
                    return (
                        <div key={item.key} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="text-sm font-black text-slate-900">{item.label}</div>
                                    <div className="mt-0.5 font-mono text-[10px] font-semibold text-slate-400">{item.key}</div>
                                </div>
                                <Badge tone={item.tone}>{fmtPct(item.value)}</Badge>
                            </div>
                            <div className="mt-3 h-2 overflow-hidden rounded-full bg-white">
                                <div
                                    className={`h-full rounded-full ${item.tone === 'good' ? 'bg-emerald-500' : item.tone === 'warn' ? 'bg-amber-500' : item.tone === 'bad' ? 'bg-rose-500' : 'bg-slate-400'}`}
                                    style={{ width: `${dangerMetric ? Math.max(2, pct) : pct}%` }}
                                />
                            </div>
                            {dangerMetric && <div className="mt-1 text-[11px] font-semibold text-slate-500">该项越低越好</div>}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function FailureBars({ insights, emptyText }: { insights: Array<{ key: string; count: number; title: string; action: string; tone: Tone }>; emptyText: string }) {
    if (!insights.length) return <ExplainEmpty title="没有明显问题" body={emptyText} />;
    const max = Math.max(...insights.map((item) => item.count), 1);
    return (
        <div className="grid gap-3 p-4">
            {insights.map((item) => (
                <div key={item.key} className="rounded-lg border border-slate-200 bg-white p-3">
                    <div className="flex items-center justify-between gap-3">
                        <div>
                            <div className="font-black text-slate-900">{item.title}</div>
                            <div className="mt-1 text-[12px] font-semibold text-slate-500">{item.action}</div>
                        </div>
                        <Badge tone={item.tone}>{item.count}</Badge>
                    </div>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                        <div className={`h-full rounded-full ${item.tone === 'bad' ? 'bg-rose-500' : item.tone === 'warn' ? 'bg-amber-500' : 'bg-sky-500'}`} style={{ width: `${Math.max(8, Math.round((item.count / max) * 100))}%` }} />
                    </div>
                </div>
            ))}
        </div>
    );
}

function DistributionMap({ title, values }: { title: string; values: Record<string, number> }) {
    const entries = Object.entries(values || {}).filter(([, value]) => Number(value) > 0);
    if (!entries.length) return null;
    const max = Math.max(...entries.map(([, value]) => Number(value)), 1);
    return (
        <div className="border-t border-slate-100 p-4">
            <SectionTitle title={title} />
            <div className="grid gap-2">
                {entries.map(([key, value]) => (
                    <div key={key} className="grid grid-cols-[160px_minmax(0,1fr)_70px] items-center gap-3 text-[13px]">
                        <span className="truncate font-semibold text-slate-600">{key}</span>
                        <div className="h-2 rounded-full bg-slate-100">
                            <div className="h-full rounded-full bg-slate-900" style={{ width: `${Math.max(8, Math.round((Number(value) / max) * 100))}%` }} />
                        </div>
                        <span className="text-right font-mono font-black">{String(value)}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

function EvaluationTabs({ value, onChange }: { value: BusinessView; onChange: (value: BusinessView) => void }) {
    return (
        <nav className="border-t border-slate-100 bg-white">
            <div className="mx-auto flex max-w-[1480px] gap-1 overflow-x-auto px-4 py-2 md:px-6">
                {NAV_ITEMS.map((item) => {
                    const Icon = item.icon;
                    const active = value === item.key;
                    return (
                        <button
                            key={item.key}
                            onClick={() => onChange(item.key)}
                            className={`flex shrink-0 items-center gap-2 rounded-md border px-3 py-2 text-left transition ${
                                active
                                    ? 'border-slate-950 bg-slate-950 text-white shadow-sm'
                                    : 'border-transparent text-slate-600 hover:border-slate-200 hover:bg-slate-50'
                            }`}
                        >
                            <Icon size={15} />
                            <span className="text-[13px] font-black">{item.title}</span>
                        </button>
                    );
                })}
            </div>
        </nav>
    );
}

function PageHeader({
    title,
    subtitle,
    description,
    badges,
}: {
    title: string;
    subtitle: string;
    description: string;
    badges: Array<React.ReactNode | null>;
}) {
    return (
        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-[0_1px_0_rgba(15,23,42,0.04)]">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <h1 className="m-0 text-[28px] font-black leading-none tracking-normal text-slate-950">{title}</h1>
                        <Badge tone="info">{subtitle}</Badge>
                    </div>
                    <p className="m-0 mt-2 text-sm font-semibold leading-relaxed text-slate-500">{description}</p>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                    {badges.filter(Boolean).map((item, index) => <React.Fragment key={index}>{item}</React.Fragment>)}
                </div>
            </div>
        </section>
    );
}

function PageIntro({ title, body }: { title: string; body: string }) {
    return (
        <section className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="text-[15px] font-black text-slate-950">{title}</div>
            <div className="mt-1 text-[13px] font-semibold leading-relaxed text-slate-500">{body}</div>
        </section>
    );
}

function MetricCard({ title, value, hint, tone = 'neutral' }: { title: string; value: React.ReactNode; hint?: string; tone?: Tone }) {
    return (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-[0_1px_0_rgba(15,23,42,0.04)]">
            <div className="text-[11px] font-black uppercase text-slate-500">{title}</div>
            <div className={`mt-2 text-[26px] font-black leading-none ${tone === 'good' ? 'text-emerald-600' : tone === 'warn' ? 'text-amber-600' : tone === 'bad' ? 'text-rose-600' : 'text-slate-950'}`}>{value}</div>
            {hint && <div className="mt-2 truncate text-[12px] font-semibold text-slate-500">{hint}</div>}
        </div>
    );
}

function DetailSection({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
    return (
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_1px_0_rgba(15,23,42,0.04)]">
            <header className="flex min-h-[56px] flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-slate-50/70 px-4 py-3">
                <div>
                    <h2 className="m-0 text-[13px] font-black uppercase text-slate-700">{title}</h2>
                    <div className="mt-1 text-[12px] font-semibold text-slate-500">详情按当前页面场景展示，开发者信息默认折叠。</div>
                </div>
                {action}
            </header>
            {children}
        </section>
    );
}

function Panel({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
    return (
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_1px_0_rgba(15,23,42,0.04)]">
            <header className="flex min-h-[48px] items-center justify-between gap-3 border-b border-slate-100 px-4 py-2">
                <h2 className="m-0 text-[12px] font-black uppercase text-slate-600">{title}</h2>
                {action}
            </header>
            {children}
        </section>
    );
}

function WorkspaceSplit({
    listTitle,
    listHint,
    list,
    detailTitle,
    detailHint,
    detailAction,
    detail,
}: {
    listTitle: string;
    listHint?: string;
    list: React.ReactNode;
    detailTitle: string;
    detailHint?: string;
    detailAction?: React.ReactNode;
    detail: React.ReactNode;
}) {
    return (
        <section className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(420px,0.85fr)]">
            <Panel title={listTitle}>
                {listHint && <div className="border-b border-slate-100 bg-slate-50/60 px-4 py-3 text-[12px] font-semibold leading-relaxed text-slate-500">{listHint}</div>}
                {list}
            </Panel>
            <InspectorPanel title={detailTitle} hint={detailHint} action={detailAction}>
                {detail}
            </InspectorPanel>
        </section>
    );
}

function InspectorPanel({ title, hint, action, children }: { title: string; hint?: string; action?: React.ReactNode; children: React.ReactNode }) {
    return (
        <aside className="min-w-0 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_8px_24px_rgba(15,23,42,0.06)] xl:sticky xl:top-4 xl:max-h-[calc(100dvh-2rem)]">
            <header className="flex min-h-[58px] flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-white px-4 py-3">
                <div className="min-w-0">
                    <h2 className="m-0 text-[13px] font-black text-slate-900">{title}</h2>
                    {hint && <div className="mt-1 truncate text-[12px] font-semibold text-slate-500">{hint}</div>}
                </div>
                {action}
            </header>
            <div className="grid gap-4 p-4 xl:max-h-[calc(100dvh-5.75rem)] xl:overflow-y-auto xl:overscroll-contain">
                {children}
            </div>
        </aside>
    );
}

function ConclusionCard({ title, body, tone = 'neutral', action }: { title: string; body: React.ReactNode; tone?: Tone; action?: React.ReactNode }) {
    const Icon = tone === 'good' ? CheckCircle2 : tone === 'bad' ? XCircle : tone === 'warn' ? AlertTriangle : Sparkles;
    return (
        <div className={`rounded-lg border p-4 ${toneClass(tone)}`}>
            <div className="flex items-start gap-3">
                <Icon size={20} className="mt-0.5" />
                <div className="min-w-0 flex-1">
                    <div className="font-black">{title}</div>
                    <div className="mt-1 text-[13px] font-semibold leading-relaxed opacity-90">{body}</div>
                </div>
                {action}
            </div>
        </div>
    );
}

function ActionTile({ title, body, onClick }: { title: string; body: string; onClick: () => void }) {
    return (
        <button onClick={onClick} className="rounded-lg border border-slate-200 bg-white p-4 text-left transition hover:border-slate-300 hover:bg-slate-50">
            <div className="font-black text-slate-950">{title}</div>
            <div className="mt-1 text-[12px] font-semibold leading-relaxed text-slate-500">{body}</div>
        </button>
    );
}

function MiniFact({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="text-[11px] font-black uppercase text-slate-500">{label}</div>
            <div className="mt-1 truncate text-sm font-black text-slate-900">{renderCell(value || 'n/a')}</div>
        </div>
    );
}

function SimpleTable({ headers, rows, empty, minWidth = '860px' }: { headers: string[]; rows: React.ReactNode[][]; empty?: string; minWidth?: string }) {
    return (
        <div className="overflow-auto">
            <table className="w-full border-collapse text-[12px]" style={{ minWidth }}>
                <thead className="sticky top-0 z-10">
                    <tr className="border-b border-slate-200 bg-slate-50/95">
                        {headers.map((header) => <th key={header} className="px-3 py-2 text-left font-black text-slate-500">{header}</th>)}
                    </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                    {rows.map((row, index) => (
                        <tr key={index} className="hover:bg-sky-50/50">
                            {row.map((cell, cellIndex) => <td key={cellIndex} className="px-3 py-2.5 align-top font-semibold text-slate-700">{renderCell(cell)}</td>)}
                        </tr>
                    ))}
                    {!rows.length && <tr><td colSpan={headers.length}><ExplainEmpty title={empty || '暂无数据'} body="当前筛选窗口内没有可展示的数据。" /></td></tr>}
                </tbody>
            </table>
        </div>
    );
}

function SectionTitle({ title, hint }: { title: string; hint?: string }) {
    return (
        <div className="mb-2">
            <div className="text-[12px] font-black text-slate-700">{title}</div>
            {hint && <div className="mt-0.5 text-[12px] font-semibold text-slate-500">{hint}</div>}
        </div>
    );
}

function Badge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: Tone }) {
    return <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-black leading-5 ${toneClass(tone)}`}>{children}</span>;
}

function ExplainEmpty({ title, body }: { title: string; body: string }) {
    return (
        <div className="p-6 text-center">
            <div className="font-black text-slate-700">{title}</div>
            <div className="mx-auto mt-2 max-w-md text-sm font-semibold leading-relaxed text-slate-500">{body}</div>
        </div>
    );
}

function DeveloperDetails({ value, defaultOpen = false }: { value: unknown; defaultOpen?: boolean }) {
    return (
        <details className="rounded-lg border border-slate-200 bg-slate-50 p-3" open={defaultOpen}>
            <summary className="cursor-pointer text-[12px] font-black text-slate-600">开发者详情 Raw JSON</summary>
            <pre className="mt-3 max-h-[420px] overflow-auto rounded-md border border-slate-800 bg-[#0d1117] p-3 font-mono text-[11px] leading-relaxed text-slate-200">
                {JSON.stringify(value || {}, null, 2)}
            </pre>
        </details>
    );
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
    return <input {...props} className={`h-9 rounded-md border border-slate-200 bg-white px-3 text-[13px] font-semibold text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-sky-500 focus:ring-2 focus:ring-sky-100 ${props.className || ''}`} />;
}

function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
    return <select {...props} className={`h-9 rounded-md border border-slate-200 bg-white px-3 text-[13px] font-semibold text-slate-800 outline-none transition focus:border-sky-500 focus:ring-2 focus:ring-sky-100 ${props.className || ''}`} />;
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
    return (
        <label className="flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <span>{label}</span>
            <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
        </label>
    );
}

function Button({ children, tone = 'dark', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: 'dark' | 'light' | 'danger' | 'green' }) {
    const cls = tone === 'dark'
        ? 'border-slate-950 bg-slate-950 text-white hover:bg-slate-800'
        : tone === 'danger'
          ? 'border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100'
          : tone === 'green'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
            : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50';
    return <button {...props} className={`inline-flex h-9 items-center justify-center gap-1 rounded-md border px-3 text-[12px] font-black transition ${cls} ${props.className || ''}`}>{children}</button>;
}

function Segmented({ value, options, onChange }: { value: string; options: Array<[string, string]>; onChange: (value: string) => void }) {
    return (
        <div className="grid grid-cols-2 rounded-lg border border-slate-200 bg-slate-100 p-1">
            {options.map(([key, label]) => (
                <button key={key} onClick={() => onChange(key)} className={`h-8 rounded-md px-3 text-[12px] font-black transition ${value === key ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500'}`}>{label}</button>
            ))}
        </div>
    );
}
