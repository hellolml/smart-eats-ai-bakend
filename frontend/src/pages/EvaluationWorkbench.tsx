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
    overall_quality: '综合质量',
    tool_error_rate: '工具失败率',
    provider_error_rate: '模型服务失败率',
    schema_compliance: '结构合规',
    no_leak: '无敏感泄露',
    repeated_action_rate: '重复动作率',
    recovery_rate: '出错恢复率',
    fallback_rate: '兜底回答率',
    latency_p95_ms: 'P95 响应耗时',
    latency_p99_ms: 'P99 响应耗时',
};

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

const metricLabel = (value?: string | null) => METRIC_COPY[value || ''] || value || '未知指标';

const failureInfo = (value?: string | null) => FAILURE_COPY[value || 'none'] || {
    title: value || '未知问题',
    action: '查看执行过程和开发者详情定位原因。',
    tone: 'warn' as Tone,
};

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

function buildQualityOverviewView(overview: any) {
    const monitoring = overview?.monitoring || {};
    const safety = overview?.safety || {};
    return {
        totalRuns: monitoring.total_runs || overview?.total_runs || 0,
        successRate: monitoring.task_success_proxy ?? 0,
        failedRuns: Math.max(0, Math.round((monitoring.total_runs || 0) * (1 - Number(monitoring.task_success_proxy || 0)))),
        pendingReviews: overview?.pending_reviews || 0,
        toolErrorRate: monitoring.tool_error_rate || 0,
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
    const failure = failureInfo(latest.failure_class);
    return {
        sessionId: session?.session_id,
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
        conclusion: latest.status === 'completed' && Number(quality) >= 0.8 ? '这段会话完成得不错' : '这段会话需要复核',
        reason: failedTools.length ? `有 ${failedTools.length} 个工具调用失败` : failure.title,
        nextAction: failedTools.length ? '查看工具失败原因，必要时转人工审核。' : failure.action,
        turns,
    };
}

function buildExecutionTimelineView(detail: any) {
    const spans = Array.isArray(detail?.spans) ? detail.spans : [];
    const events = Array.isArray(detail?.events) ? detail.events : [];
    if (spans.length) {
        return spans.map((span: any, index: number) => {
            const info = spanInfo(span.span_type);
            return {
                id: span.id || `${span.span_type}-${index}`,
                index,
                title: info.title,
                subtitle: span.name || span.span_type,
                tone: span.status === 'error' ? 'bad' as Tone : info.tone,
                icon: info.icon,
                duration: span.duration_ms,
                status: span.status || 'ok',
                detail: span.error || span.output?.output_preview || '',
                raw: span,
            };
        });
    }
    return events.map((event: any, index: number) => {
        const info = spanInfo(event.event_type);
        return {
            id: `${event.event_type}-${index}`,
            index,
            title: info.title,
            subtitle: event.tool_name || event.event_type,
            tone: event.event_type === 'error' ? 'bad' as Tone : info.tone,
            icon: info.icon,
            duration: event.duration_ms,
            status: event.event_type,
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
                experimentData,
                evaluatorData,
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
                appApi.evaluations.listHubExperiments(),
                appApi.evaluations.listHubEvaluators(),
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
            const datasetRecords = datasetData.datasets || [];
            setDatasets(datasetRecords);
            setExperiments(experimentData.records || []);
            setEvaluators(evaluatorData.evaluators || []);
            if (!datasetCases.length && datasetRecords.length) {
                try {
                    const firstDataset = datasetRecords[0]?.name || datasetRecords[0]?.suite || 'regression';
                    const cases = await appApi.evaluations.listHubDatasetCases(firstDataset);
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
            return <OfflineEvaluation reports={reports} jobs={jobs} />;
        }
        if (view === 'data') {
            return <DataFeedbackLoop datasets={datasets} cases={datasetCases} experiments={experiments} evaluators={evaluators} />;
        }
        return <ExpertConsole overview={overview} sessions={sessions} traces={traces} selectedTrace={selectedTrace} selectedSession={selectedSession} failures={failures} cost={cost} safety={safety} />;
    }, [view, quality, overview, sessions, failures, selectedSession, expertMode, selectedTrace, traces, cost, safety, reviews, reports, jobs, datasets, datasetCases, experiments, evaluators, loadCore]);

    return (
        <div className="min-h-[100dvh] bg-[#eef2f6] text-slate-950">
            <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
                <div className="flex min-h-[66px] items-center gap-4 px-4 md:px-5">
                    <div className="flex min-w-[260px] items-center gap-3">
                        <div className="grid h-11 w-11 place-items-center rounded-lg border border-slate-950 bg-slate-950 text-white">
                            <ClipboardCheck size={20} />
                        </div>
                        <div>
                            <div className="text-[15px] font-black">AgentEval Hub</div>
                            <div className="text-[12px] font-semibold text-slate-500">质检运营台 · Quality Ops</div>
                        </div>
                    </div>
                    <div className="hidden h-9 w-px bg-slate-200 lg:block" />
                    <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
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
            </header>

            <div className="grid grid-cols-1 md:grid-cols-[272px_minmax(0,1fr)]">
                <aside className="hidden min-h-[calc(100dvh-66px)] border-r border-slate-200 bg-[#f8fafc] md:block">
                    <div className="sticky top-[66px] grid gap-4 p-4">
                        <div className="rounded-lg border border-slate-200 bg-white p-4">
                            <div className="text-[11px] font-black uppercase text-slate-500">今日质检结论</div>
                            <div className="mt-2 text-lg font-black text-slate-950">{quality.conclusion}</div>
                            <div className="mt-1 text-[12px] font-semibold leading-relaxed text-slate-500">{quality.nextAction}</div>
                        </div>
                        <nav className="grid gap-1">
                            {NAV_ITEMS.map((item) => {
                                const Icon = item.icon;
                                const active = view === item.key;
                                return (
                                    <button
                                        key={item.key}
                                        onClick={() => setView(item.key)}
                                        className={`grid grid-cols-[34px_minmax(0,1fr)] items-center gap-2 rounded-lg border px-3 py-2.5 text-left transition ${active ? 'border-slate-300 bg-white shadow-[0_1px_0_rgba(15,23,42,0.05)]' : 'border-transparent text-slate-600 hover:border-slate-200 hover:bg-white'}`}
                                    >
                                        <span className={`grid h-8 w-8 place-items-center rounded-md ${active ? 'bg-slate-950 text-white' : 'bg-slate-100 text-slate-500'}`}>
                                            <Icon size={16} />
                                        </span>
                                        <span>
                                            <span className="block text-[13px] font-black">{item.title}</span>
                                            <span className="block text-[11px] font-semibold text-slate-500">{item.subtitle}</span>
                                        </span>
                                    </button>
                                );
                            })}
                        </nav>
                    </div>
                </aside>

                <main className="min-w-0 p-3 md:p-5">
                    <div className="mb-4 flex gap-2 overflow-x-auto md:hidden">
                        {NAV_ITEMS.map((item) => (
                            <button key={item.key} onClick={() => setView(item.key)} className={`whitespace-nowrap rounded-md border px-3 py-2 text-[12px] font-black ${view === item.key ? 'border-slate-950 bg-slate-950 text-white' : 'border-slate-200 bg-white text-slate-600'}`}>{item.title}</button>
                        ))}
                    </div>
                    <section className="mb-5 rounded-lg border border-slate-200 bg-white p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <h1 className="m-0 text-[28px] font-black leading-none">{activeNav.title}</h1>
                                    <Badge tone="info">{activeNav.subtitle}</Badge>
                                    {expertMode && <Badge tone="warn">专家视图已开启</Badge>}
                                </div>
                                <p className="m-0 mt-2 text-sm font-semibold text-slate-500">
                                    默认看结论、原因和下一步；需要排障时再打开专家视图。
                                </p>
                            </div>
                            <div className="flex flex-wrap gap-2">
                                {loading && <Badge tone="info">同步中</Badge>}
                                {message && <Badge tone="good">{message}</Badge>}
                                {error && <Badge tone="bad">{error}</Badge>}
                                <Badge tone={scoreTone(quality.successRate)}>成功 {fmtPct(quality.successRate)}</Badge>
                                <Badge tone={quality.pendingReviews ? 'warn' : 'good'}>待审 {quality.pendingReviews}</Badge>
                            </div>
                        </div>
                    </section>
                    {content}
                </main>
            </div>
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
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-8">
                <MetricCard title="对话数" value={quality.totalRuns} hint="已采集会话轮次" />
                <MetricCard title="完成率" value={fmtPct(quality.successRate)} tone={scoreTone(quality.successRate)} hint="任务成功 proxy" />
                <MetricCard title="失败会话" value={quality.failedRuns} tone={quality.failedRuns ? 'warn' : 'good'} />
                <MetricCard title="待人工审核" value={quality.pendingReviews} tone={quality.pendingReviews ? 'warn' : 'good'} />
                <MetricCard title="工具失败" value={fmtPct(quality.toolErrorRate)} tone={Number(quality.toolErrorRate) > 0 ? 'bad' : 'good'} />
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
                            <button key={item.session_id} onClick={() => onOpenSession(item.session_id)} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-left transition hover:bg-white">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="truncate font-mono text-[12px] font-black text-sky-700">{item.session_id}</span>
                                    <Badge tone={scoreTone(item.latest_score)}>{fmtPct(item.latest_score)}</Badge>
                                </div>
                                <div className="mt-2 text-[12px] font-semibold text-slate-600">
                                    {sceneLabel(item.scene)} · {item.turn_count || 0} 轮 · {fmtMs(item.latency_ms)}
                                </div>
                            </button>
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
    return (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_460px]">
            <Panel title="会话列表">
                <SimpleTable
                    headers={['会话', '最近场景', '结果', '质量', '轮次', '耗时', '模型']}
                    empty="暂无线上会话"
                    rows={sessions.map((item) => [
                        <button className="font-mono text-sky-700 underline-offset-2 hover:underline" onClick={() => onOpenSession(item.session_id)}>{item.session_id}</button>,
                        sceneLabel(item.scene),
                        <Badge tone={item.status === 'completed' ? 'good' : 'bad'}>{item.status === 'completed' ? '已完成' : '异常'}</Badge>,
                        <Badge tone={scoreTone(item.latest_score)}>{fmtPct(item.latest_score)}</Badge>,
                        item.turn_count || 0,
                        fmtMs(item.latency_ms),
                        item.model || 'n/a',
                    ])}
                />
            </Panel>
            <Panel title="会话质检结果">
                {!vm ? (
                    <ExplainEmpty title="选择左侧一段会话" body="这里会用普通话解释这段会话是否完成、Agent 做了哪些步骤、是否需要人工处理。" />
                ) : (
                    <div className="grid max-h-[calc(100dvh-280px)] gap-4 overflow-auto p-4">
                        <ConclusionCard title={vm.conclusion} body={`${vm.reason}。${vm.nextAction}`} tone={scoreTone(vm.quality)} />
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
                                                <Badge tone={scoreTone(run.overall_quality)}>{fmtPct(run.overall_quality)}</Badge>
                                                <span className="ml-auto text-[12px] font-semibold text-slate-500">{fmtMs(run.latency_ms)}</span>
                                            </div>
                                            <div className="mt-2 text-[13px] font-semibold text-slate-600">
                                                {sceneLabel(run.scene)} · {run.model_name || '未知模型'} · 工具 {tools.length} 个
                                            </div>
                                            <div className="mt-3">
                                                <Button tone="light" onClick={() => onOpenTrace(traceId)}>查看执行过程</Button>
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
            </Panel>
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
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_520px]">
            <Panel title="执行轨迹列表">
                <SimpleTable
                    headers={['Trace 执行轨迹', '会话', '场景', '结果', '质量', '步骤', '耗时']}
                    empty="暂无执行轨迹"
                    rows={traces.map((item) => [
                        <button className="font-mono text-sky-700 underline-offset-2 hover:underline" onClick={() => onOpenTrace(item.trace_id || item.id)}>{item.trace_id || item.id}</button>,
                        <span className="font-mono text-[12px]">{item.session_id}</span>,
                        sceneLabel(item.scene),
                        <Badge tone={item.status === 'completed' ? 'good' : 'bad'}>{item.status === 'completed' ? '完成' : '异常'}</Badge>,
                        <Badge tone={scoreTone(item.score)}>{fmtPct(item.score)}</Badge>,
                        item.span_count ?? 0,
                        fmtMs(item.latency_ms),
                    ])}
                />
            </Panel>
            <Panel
                title="执行过程解读"
                action={selected && (
                    <div className="flex gap-2">
                        <Button tone="light" onClick={() => onAddToDataset(traceId)}>加入数据集</Button>
                        <Button tone="light" onClick={() => onSendReview(run.id)}>送人工审核</Button>
                    </div>
                )}
            >
                {!selected ? (
                    <ExplainEmpty title="选择一条 Trace 执行轨迹" body="这里会按时间线解释 Agent 先理解了什么、调用了什么工具、在哪里失败或完成。" />
                ) : (
                    <div className="grid max-h-[calc(100dvh-280px)] gap-4 overflow-auto p-4">
                        <ConclusionCard
                            title={run.status === 'completed' && Number(run.overall_quality || 0) >= 0.8 ? '这次执行完成了任务' : '这次执行需要复核'}
                            body={`${sceneLabel(run.scene)} / ${run.worker || run.agent_id || '未知执行器'} / ${run.model_name || '未知模型'} / ${fmtMs(run.latency_ms)}`}
                            tone={scoreTone(run.overall_quality)}
                        />
                        <Timeline items={timeline} />
                        {selected.tool_calls?.length > 0 && (
                            <div>
                                <SectionTitle title="工具调用结果" hint="工具失败通常是影响回答质量的第一优先排查点。" />
                                <ToolList tools={selected.tool_calls} />
                            </div>
                        )}
                        {expertMode && <DeveloperDetails value={selected} />}
                    </div>
                )}
            </Panel>
        </div>
    );
}

function FailureInsights({ failures, traces, onOpenTrace }: { failures: any; traces: any[]; onOpenTrace: (traceId: string) => void }) {
    const insights = buildFailureInsightView(failures);
    return (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <Panel title="问题归因">
                <FailureBars insights={insights} emptyText="当前窗口内没有明确失败归因。" />
            </Panel>
            <Panel title="低分或异常执行">
                <div className="grid gap-2 p-4">
                    {traces.filter((item) => item.status !== 'completed' || Number(item.score || item.overall_quality || 1) < 0.8).slice(0, 8).map((item) => (
                        <button key={item.id || item.trace_id} onClick={() => onOpenTrace(item.trace_id || item.id)} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-left hover:bg-white">
                            <div className="flex items-center justify-between gap-2">
                                <span className="truncate font-mono text-[12px] font-black text-sky-700">{item.trace_id || item.id}</span>
                                <Badge tone={scoreTone(item.score || item.overall_quality)}>{fmtPct(item.score || item.overall_quality)}</Badge>
                            </div>
                            <div className="mt-2 text-[12px] font-semibold text-slate-600">{sceneLabel(item.scene)} · {item.status || 'unknown'} · {fmtMs(item.latency_ms)}</div>
                        </button>
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
                            </div>
                        </div>
                    );
                })}
                {!records.length && <ExplainEmpty title="暂无待审核会话" body="低分、工具失败、安全风险或人工标记的会话会出现在这里。" />}
            </div>
        </Panel>
    );
}

function OfflineEvaluation({ reports, jobs }: { reports: any[]; jobs: any[] }) {
    return (
        <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="发布前回归检查 Runs">
                <SimpleTable
                    headers={['报告', '范围', '通过率', '失败', '耗时']}
                    empty="暂无离线评测报告"
                    rows={reports.map((item) => [
                        <span className="font-mono text-sky-700">{item.name}</span>,
                        `${item.suite || 'n/a'} / ${item.runner || 'n/a'}`,
                        <Badge tone={scoreTone(item.overall_success_rate)}>{fmtPct(item.overall_success_rate)}</Badge>,
                        item.failed_cases ?? 0,
                        `${Number(item.duration_seconds || 0).toFixed(2)}s`,
                    ])}
                />
            </Panel>
            <Panel title="Web 触发评测任务">
                <SimpleTable
                    headers={['任务', '状态', '范围', '报告', '创建时间']}
                    empty="暂无评测任务"
                    rows={jobs.map((item) => [
                        <span className="font-mono text-sky-700">{item.id}</span>,
                        <Badge tone={item.status === 'succeeded' ? 'good' : item.status === 'failed' ? 'bad' : 'warn'}>{item.status}</Badge>,
                        `${item.runner || 'n/a'} / ${item.suite || 'n/a'}`,
                        item.report_name || 'n/a',
                        fmtTime(item.created_at),
                    ])}
                />
            </Panel>
        </div>
    );
}

function DataFeedbackLoop({ datasets, cases, experiments, evaluators }: { datasets: any[]; cases: any[]; experiments: any[]; evaluators: any[] }) {
    return (
        <div className="grid gap-4">
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <MetricCard title="Dataset 数据集" value={datasets.length} />
                <MetricCard title="Regression Case" value={cases.length} />
                <MetricCard title="Experiment 实验" value={experiments.length} />
                <MetricCard title="Evaluator 评测器" value={evaluators.length} />
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
            <div className="grid gap-4 xl:grid-cols-2">
                <Panel title="数据集">
                    <SimpleTable
                        headers={['名称', '版本', '状态', 'Case 数']}
                        empty="暂无数据集"
                        rows={datasets.map((item) => [
                            item.name || item.suite || 'n/a',
                            item.version || 'n/a',
                            <Badge tone="info">{item.status || 'active'}</Badge>,
                            item.total_cases || 0,
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
                                <span className="font-black text-slate-900">{item.title}</span>
                                <Badge tone={item.tone}>{item.status}</Badge>
                                <span className="ml-auto text-[12px] font-semibold text-slate-500">{fmtMs(item.duration)}</span>
                            </div>
                            <div className="mt-1 text-[13px] font-semibold text-slate-600">{item.subtitle}</div>
                            {item.detail && <div className="mt-2 rounded-md bg-slate-50 px-3 py-2 text-[12px] font-semibold text-slate-600">{displayValue(item.detail)}</div>}
                        </div>
                    </div>
                );
            })}
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

function MetricCard({ title, value, hint, tone = 'neutral' }: { title: string; value: React.ReactNode; hint?: string; tone?: Tone }) {
    return (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-[0_1px_0_rgba(15,23,42,0.04)]">
            <div className="text-[11px] font-black uppercase text-slate-500">{title}</div>
            <div className={`mt-2 text-[26px] font-black leading-none ${tone === 'good' ? 'text-emerald-600' : tone === 'warn' ? 'text-amber-600' : tone === 'bad' ? 'text-rose-600' : 'text-slate-950'}`}>{value}</div>
            {hint && <div className="mt-2 truncate text-[12px] font-semibold text-slate-500">{hint}</div>}
        </div>
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

function SimpleTable({ headers, rows, empty }: { headers: string[]; rows: React.ReactNode[][]; empty?: string }) {
    return (
        <div className="overflow-auto">
            <table className="w-full min-w-[860px] border-collapse text-[12px]">
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
