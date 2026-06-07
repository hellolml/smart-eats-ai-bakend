import React, { useEffect, useState, useCallback } from 'react';
import { appApi, ApiError } from '@/services/app-api';
import type {
    EvalReportSummary,
    EvalReport,
    EvalCaseResult,
    EvalCaseDetailResponse,
    EvalCompareResponse,
    EvalTraceEvent,
    RealtimeEvalRecord,
    RealtimeEvalSummaryResponse,
} from '@/services/app-api';
import EvaluationAccessGate from '@/components/EvaluationAccessGate';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const fmtPct = (v: unknown): string =>
    Number.isFinite(Number(v)) ? `${(Number(v) * 100).toFixed(1)}%` : 'n/a';

const fmtMs = (v: unknown): string =>
    Number.isFinite(Number(v)) ? `${Math.round(Number(v))}ms` : 'n/a';

const fmtShortTime = (v: unknown): string =>
    v ? String(v).replace('T', ' ').slice(0, 16) : 'n/a';

const scoreClass = (v: unknown): string =>
    Number(v) >= 0.8 ? 'text-emerald-600' : Number(v) >= 0.5 ? 'text-amber-600' : 'text-red-600';

const barColor = (v: unknown): string =>
    Number(v) >= 0.8 ? 'bg-emerald-500' : Number(v) >= 0.5 ? 'bg-amber-500' : 'bg-red-500';

const statusColor = (v: unknown): string =>
    Number(v) >= 0.8
        ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
        : Number(v) >= 0.5
          ? 'bg-amber-50 text-amber-700 border-amber-200'
          : 'bg-red-50 text-red-700 border-red-200';

const deltaSignClass = (v: number): string => (v >= 0 ? 'text-emerald-600' : 'text-red-600');

const fmtDelta = (v: unknown, pct = false): string => {
    const n = Number(v || 0);
    const prefix = n > 0 ? '+' : '';
    return pct ? `${prefix}${(n * 100).toFixed(1)}%` : `${prefix}${n.toFixed(2)}`;
};

const LABELS: Record<string, Record<string, string>> = {
    scene: {
        eat_out: '外出吃饭',
        cook_home: '在家做饭',
        route: '路线导航',
        travel_planner: '旅行规划',
        chat: '通用聊天',
    },
    category: {
        normal: '正常请求',
        boundary: '边界输入',
        tool_failure: '工具失败',
        safety: '安全风险',
        regression: '回归用例',
    },
    priority: { p0: 'P0 必过', p1: 'P1 重要', p2: 'P2 常规' },
    metric: {
        task_success: '任务成功',
        intent_accuracy: '意图准确',
        tool_accuracy: '工具准确',
        schema_compliance: '结构合规',
        safety_score: '安全得分',
        no_leak: '无泄露',
        recovery_score: '恢复能力',
        constraint_satisfaction: '约束满足',
        efficiency: '执行效率',
    },
    failureGroup: {
        by_error_reason: '按错误原因',
        by_case: '按用例',
        by_metric: '按指标',
        by_scene: '按场景',
        by_category: '按类别',
        by_tool: '按工具',
        by_worker: '按执行器',
        by_failure_class: '按失败类型',
    },
    failureClass: {
        none: '无失败',
        provider: '模型/Provider',
        tool_api: '工具/API',
        agent_quality: 'Agent 质量',
        eval_framework: '评测框架',
    },
};

const label = (kind: string, val?: string | null): string =>
    LABELS[kind]?.[val || ''] || val || 'n/a';

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

type View = 'overview' | 'history' | 'compare' | 'detail' | 'analysis' | 'realtime';

const TAB_ITEMS: { key: View; label: string }[] = [
    { key: 'realtime', label: '实时监控' },
    { key: 'overview', label: '总览' },
    { key: 'history', label: '运行历史' },
    { key: 'compare', label: '运行对比' },
    { key: 'detail', label: '用例详情' },
    { key: 'analysis', label: '失败分析' },
];

const MetricCard: React.FC<{ title: string; value: string; hint?: string }> = ({ title, value, hint }) => (
    <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-3 flex flex-col justify-between min-h-[88px] relative overflow-hidden">
        <div className="absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-blue-500 to-emerald-500" />
        <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wide">{title}</span>
        <strong className="font-mono text-2xl leading-none">{value}</strong>
        {hint && <span className="text-[11px] text-gray-400 truncate">{hint}</span>}
    </div>
);

const BarRow: React.FC<{ name: string; rate: number; kind: string }> = ({ name, rate, kind }) => (
    <div className="grid grid-cols-[140px_minmax(100px,1fr)_56px] gap-2 items-center min-h-[28px] text-[13px] text-gray-600">
        <span className="truncate">{label(kind, name)}</span>
        <div className="h-[10px] bg-gray-100 border border-gray-200 rounded-full overflow-hidden">
            <div className={`h-full rounded-full ${barColor(rate)}`} style={{ width: `${Math.max(0, Math.min(100, rate * 100))}%` }} />
        </div>
        <span className={`font-mono font-bold text-right ${scoreClass(rate)}`}>{fmtPct(rate)}</span>
    </div>
);

const TraceTimeline: React.FC<{ events: EvalTraceEvent[] }> = ({ events }) => {
    if (!events.length) {
        return <div className="p-6 text-center text-gray-400 text-sm">当前报告没有 trace timeline。请用新版 run_eval.py 重新生成报告。</div>;
    }
    const borderColor: Record<string, string> = {
        context: 'border-l-blue-500',
        tool_call: 'border-l-violet-600',
        tool_result: 'border-l-emerald-500',
        final: 'border-l-emerald-500',
        recovery: 'border-l-amber-500',
        error: 'border-l-red-500',
    };
    return (
        <div className="p-4 grid gap-2">
            {events.map((ev, idx) => (
                <div key={idx} className={`border border-gray-200 border-l-4 ${borderColor[ev.event_type] || 'border-l-gray-400'} rounded-lg p-3 bg-white shadow-sm grid gap-1`}>
                    <div className="flex justify-between gap-3 font-bold text-[13px] text-gray-700">
                        <span>{ev.label || ev.event_type}</span>
                        <span className="font-mono text-gray-400 text-[12px]">
                            {ev.event_type} #{ev.index}
                            {ev.duration_ms ? ` / ${fmtMs(ev.duration_ms)}` : ''}
                        </span>
                    </div>
                    {ev.tool_name && (
                        <span className="inline-flex items-center self-start px-2 py-0.5 border border-gray-200 bg-gray-50 rounded-full text-[11px] font-mono">
                            tool {ev.tool_name}
                        </span>
                    )}
                    {ev.data && Object.keys(ev.data).length > 0 && (
                        <pre className="m-0 whitespace-pre-wrap text-gray-400 font-mono text-[12px] leading-relaxed">
                            {JSON.stringify(ev.data, null, 2)}
                        </pre>
                    )}
                </div>
            ))}
        </div>
    );
};

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

const EvaluationWorkbenchInner: React.FC = () => {
    const [activeView, setActiveView] = useState<View>('overview');
    const [reports, setReports] = useState<EvalReportSummary[]>([]);
    const [selectedReportName, setSelectedReportName] = useState('latest.json');
    const [report, setReport] = useState<EvalReport | null>(null);
    const [source, setSource] = useState<'db' | 'json'>('json');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Filters
    const [sceneFilter, setSceneFilter] = useState('');
    const [categoryFilter, setCategoryFilter] = useState('');
    const [searchQuery, setSearchQuery] = useState('');

    // Compare
    const [baselineName, setBaselineName] = useState('');
    const [candidateName, setCandidateName] = useState('');
    const [compareData, setCompareData] = useState<EvalCompareResponse | null>(null);
    const [compareLoading, setCompareLoading] = useState(false);

    // Case detail
    const [caseDetail, setCaseDetail] = useState<EvalCaseDetailResponse | null>(null);
    const [caseLoading, setCaseLoading] = useState(false);

    // Raw JSON drawer
    const [rawOpen, setRawOpen] = useState(false);

    // Realtime eval
    const [realtimeRecords, setRealtimeRecords] = useState<RealtimeEvalRecord[]>([]);
    const [realtimeSummary, setRealtimeSummary] = useState<RealtimeEvalSummaryResponse | null>(null);
    const [realtimeLoading, setRealtimeLoading] = useState(false);
    const [realtimeHours, setRealtimeHours] = useState(24);

    const loadReportList = useCallback(async () => {
        try {
            const data = await appApi.evaluations.listReports();
            setReports(data.reports || []);
        } catch {
            // silent — reports will be empty
        }
    }, []);

    const loadReport = useCallback(async (name: string) => {
        setLoading(true);
        setError(null);
        try {
            const data = await appApi.evaluations.getReport(name);
            setSource(data.source);
            setSelectedReportName(data.selected);
            if (data.available && data.report) {
                setReport(data.report);
            } else {
                setReport(null);
                setError(data.message || '未找到评测报告');
            }
            // Update report list from response
            if (data.reports?.length) {
                setReports((prev) => (prev.length ? prev : data.reports));
            }
        } catch (e) {
            setError(e instanceof ApiError ? e.message : '加载失败');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadReport('latest.json').then(() => loadReportList());
    }, [loadReport, loadReportList]);

    // Derived data
    const results = report?.results || [];
    const filteredResults = results.filter((item) => {
        if (sceneFilter && item.scene !== sceneFilter) return false;
        if (categoryFilter && item.category !== categoryFilter) return false;
        if (searchQuery) {
            const haystack = [item.case_id, item.scene, item.category, item.task, JSON.stringify(item.trials || [])]
                .join(' ')
                .toLowerCase();
            if (!haystack.includes(searchQuery.toLowerCase())) return false;
        }
        return true;
    });

    const scenes = [...new Set(results.map((r) => r.scene).filter(Boolean))].sort() as string[];
    const categories = [...new Set(results.map((r) => r.category).filter(Boolean))].sort() as string[];

    // Compare selectors initialization
    useEffect(() => {
        if (reports.length >= 2 && !baselineName) {
            setBaselineName(reports[1].name);
            setCandidateName(reports[0].name);
        } else if (reports.length === 1 && !baselineName) {
            setBaselineName(reports[0].name);
            setCandidateName(reports[0].name);
        }
    }, [reports, baselineName]);

    const handleCompare = async () => {
        if (!baselineName || !candidateName) return;
        setCompareLoading(true);
        try {
            const data = await appApi.evaluations.compareReports(baselineName, candidateName);
            setCompareData(data);
        } catch (e) {
            setError(e instanceof ApiError ? e.message : '对比失败');
        } finally {
            setCompareLoading(false);
        }
    };

    const loadCaseDetail = async (caseId: string) => {
        setCaseLoading(true);
        try {
            const data = await appApi.evaluations.getCaseDetail(selectedReportName, caseId);
            setCaseDetail(data);
            setActiveView('detail');
        } catch (e) {
            setError(e instanceof ApiError ? e.message : '加载用例详情失败');
        } finally {
            setCaseLoading(false);
        }
    };

    const openReport = (name: string) => {
        loadReport(name);
        setActiveView('overview');
    };

    // Failure summary
    const failureSummary = report?.failure_summary || {};

    // Trend data (last 5 runs)
    const trendReports = reports.slice(0, 5).reverse();

    // Stats
    const failedCases = results.filter((r) => Number(r.success_rate || 0) < 1).length;
    const p0Failed = results.filter((r) => r.priority === 'p0' && Number(r.success_rate || 0) < 1).length;

    // ---------------------------------------------------------------------------
    // Render helpers
    // ---------------------------------------------------------------------------

    const renderOverview = () => (
        <div className="grid gap-4">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                <MetricCard title="总体成功率" value={fmtPct(report?.overall_success_rate)} hint="overall_success_rate" />
                <MetricCard title="P0 失败" value={String(p0Failed)} hint="必须保持为 0" />
                <MetricCard title="失败用例" value={String(failedCases)} hint={`${results.length} 条已加载`} />
                <MetricCard title="用例数" value={String(report?.total_cases ?? results.length)} hint="total_cases" />
                <MetricCard title="试验数" value={String(report?.total_trials ?? 0)} hint="total_trials" />
                <MetricCard title="耗时" value={`${Number(report?.duration_seconds || 0).toFixed(1)}s`} hint={fmtShortTime(report?.timestamp)} />
            </div>

            {/* Run Trend */}
            <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100 bg-gradient-to-b from-white to-gray-50">
                    <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">最近运行趋势</h2>
                    <span className="font-mono text-[12px] text-gray-400">最近 {trendReports.length} 次</span>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3 p-4">
                    {trendReports.length === 0 && <div className="col-span-full py-6 text-center text-gray-400 text-sm">暂无运行历史</div>}
                    {trendReports.map((item) => {
                        const rate = Number(item.overall_success_rate || 0);
                        const height = Math.max(3, Math.round(rate * 44));
                        return (
                            <div key={item.name} className="border border-gray-200 rounded-lg p-3 bg-white shadow-sm grid gap-2" title={item.name}>
                                <div className="h-[44px] flex items-end border-b border-gray-200">
                                    <div className={`w-full rounded-t ${barColor(rate)}`} style={{ minHeight: '3px', height: `${height}px` }} />
                                </div>
                                <div className="flex justify-between font-mono text-[11px] font-bold text-gray-400">
                                    <span>{fmtPct(rate)}</span>
                                    <span>{item.failed_cases ?? 0} fail</span>
                                </div>
                                <div className="flex justify-between font-mono text-[11px] text-gray-400">
                                    <span>{item.runner || 'n/a'}</span>
                                    <span>{fmtShortTime(item.timestamp)}</span>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* Scene & Category Breakdowns */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                    <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                        <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">场景表现</h2>
                        <span className="font-mono text-[12px] text-gray-400">{Object.keys(report?.scene_breakdown || {}).length}</span>
                    </div>
                    <div className="p-4 grid gap-2">
                        {Object.entries(report?.scene_breakdown || {}).map(([name, data]) => (
                            <BarRow key={name} name={name} rate={Number(data.success_rate || 0)} kind="scene" />
                        ))}
                        {!Object.keys(report?.scene_breakdown || {}).length && <div className="py-4 text-center text-gray-400 text-sm">暂无分组数据</div>}
                    </div>
                </div>
                <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                    <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                        <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">类别表现</h2>
                        <span className="font-mono text-[12px] text-gray-400">{Object.keys(report?.category_breakdown || {}).length}</span>
                    </div>
                    <div className="p-4 grid gap-2">
                        {Object.entries(report?.category_breakdown || {}).map(([name, data]) => (
                            <BarRow key={name} name={name} rate={Number(data.success_rate || 0)} kind="category" />
                        ))}
                        {!Object.keys(report?.category_breakdown || {}).length && <div className="py-4 text-center text-gray-400 text-sm">暂无分组数据</div>}
                    </div>
                </div>
            </div>

            {/* Failure Summary */}
            <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                    <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">失败聚合</h2>
                    <span className="font-mono text-[12px] text-gray-400">
                        {Object.values(failureSummary).filter((v) => v && Object.keys(v).length).length} 组
                    </span>
                </div>
                <div className="p-4 grid gap-3">
                    {Object.entries(failureSummary)
                        .filter(([, v]) => v && Object.keys(v).length)
                        .map(([group, value]) => (
                            <div key={group} className="pb-2 border-b border-gray-100 last:border-b-0 last:pb-0">
                                <div className="font-extrabold text-[12px] uppercase text-gray-500 mb-2">{label('failureGroup', group)}</div>
                                <div className="flex flex-wrap gap-1.5">
                                    {Object.entries(value || {})
                                        .sort(([, a], [, b]) => Number(typeof b === 'object' ? (b as { success_rate?: number }).success_rate : b) - Number(typeof a === 'object' ? (a as { success_rate?: number }).success_rate : a))
                                        .map(([name, count]) => (
                                            <span key={name} className="inline-flex items-center px-2 py-0.5 border border-gray-200 bg-gray-50 rounded-full text-[11px] font-mono text-gray-600">
                                                {name} {String(count)}
                                            </span>
                                        ))}
                                </div>
                            </div>
                        ))}
                    {!Object.values(failureSummary).filter((v) => v && Object.keys(v).length).length && (
                        <div className="py-4 text-center text-gray-400 text-sm">当前报告没有失败聚合</div>
                    )}
                </div>
            </div>

            {/* Case Table */}
            <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                    <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">用例结果</h2>
                    <span className="font-mono text-[12px] text-gray-400">显示 {filteredResults.length} 条</span>
                </div>
                <div className="overflow-auto">
                    <table className="w-full border-collapse min-w-[1020px] text-[13px]">
                        <thead>
                            <tr>
                                {['用例', '场景', '类别', '优先级', '成功率', '任务', '关键指标', '试验明细', '操作'].map((h) => (
                                    <th key={h} className="px-3 py-2.5 bg-gray-50 text-gray-500 text-[11px] uppercase text-left border-b border-gray-200">{h}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {filteredResults.length === 0 && (
                                <tr><td colSpan={9} className="py-8 text-center text-gray-400">没有符合当前筛选条件的用例</td></tr>
                            )}
                            {filteredResults.map((item) => {
                                const trial = (item.trials || [])[0];
                                const failures = (trial?.threshold_failures || []).map((f) => f.metric);
                                const missing = trial?.missing_metrics || [];
                                return (
                                    <tr key={item.case_id} className="hover:bg-gray-50 transition-colors">
                                        <td className="px-3 py-2.5 border-b border-gray-100 font-mono font-bold text-blue-600">{item.case_id}</td>
                                        <td className="px-3 py-2.5 border-b border-gray-100">{label('scene', item.scene)}</td>
                                        <td className="px-3 py-2.5 border-b border-gray-100">{label('category', item.category)}</td>
                                        <td className="px-3 py-2.5 border-b border-gray-100">{label('priority', item.priority)}</td>
                                        <td className={`px-3 py-2.5 border-b border-gray-100 font-mono font-bold ${scoreClass(item.success_rate)}`}>{fmtPct(item.success_rate)}</td>
                                        <td className="px-3 py-2.5 border-b border-gray-100 max-w-[340px] leading-relaxed text-gray-600">{item.task}</td>
                                        <td className="px-3 py-2.5 border-b border-gray-100">
                                            <div className="flex flex-wrap gap-1">
                                                {Object.entries(item.avg_scores || {})
                                                    .filter(([k]) => ['task_success', 'intent_accuracy', 'tool_accuracy', 'schema_compliance', 'safety_score', 'no_leak', 'recovery_score'].includes(k))
                                                    .map(([k, v]) => (
                                                        <span key={k} className="inline-flex px-1.5 py-0.5 border border-gray-200 bg-gray-50 rounded-full text-[11px] font-mono">
                                                            {label('metric', k)} {fmtPct(v)}
                                                        </span>
                                                    ))}
                                            </div>
                                        </td>
                                        <td className="px-3 py-2.5 border-b border-gray-100 text-gray-400 text-[12px]">
                                            <div className="grid gap-1">
                                                {trial && (
                                                    <>
                                                        <span>实际场景 {label('scene', trial.actual_scene)}</span>
                                                        {trial.actual_worker && <span>执行器 {trial.actual_worker}</span>}
                                                        {trial.error_reason && <span>错误 {trial.error_reason}</span>}
                                                        {failures.length > 0 && <span>阈值失败 {failures.map((f) => label('metric', f)).join(', ')}</span>}
                                                        {missing.length > 0 && <span>缺失指标 {missing.map((m) => label('metric', m)).join(', ')}</span>}
                                                        <span>工具调用：{(trial.tool_calls || []).join(', ') || '无'}</span>
                                                    </>
                                                )}
                                            </div>
                                        </td>
                                        <td className="px-3 py-2.5 border-b border-gray-100">
                                            <button
                                                className="px-3 py-1 border border-gray-300 bg-white rounded-md text-[12px] font-bold hover:bg-gray-50 transition"
                                                onClick={() => loadCaseDetail(item.case_id)}
                                            >
                                                查看
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );

    const renderHistory = () => (
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
            <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">运行历史</h2>
                <span className="font-mono text-[12px] text-gray-400">{reports.length} 次运行</span>
            </div>
            <div className="overflow-auto">
                <table className="w-full border-collapse min-w-[1020px] text-[13px]">
                    <thead>
                        <tr>
                            {['报告文件', '时间', 'Suite', 'Runner', '用例', '试验', '成功率', '失败', 'P0 失败', '耗时', '操作'].map((h) => (
                                <th key={h} className="px-3 py-2.5 bg-gray-50 text-gray-500 text-[11px] uppercase text-left border-b border-gray-200">{h}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {reports.length === 0 && (
                            <tr><td colSpan={11} className="py-8 text-center text-gray-400">暂无运行历史</td></tr>
                        )}
                        {reports.map((item) => (
                            <tr key={item.name} className={`hover:bg-gray-50 transition-colors ${item.name === selectedReportName ? 'bg-blue-50/50' : ''}`}>
                                <td className="px-3 py-2.5 border-b border-gray-100 font-mono">{item.name}</td>
                                <td className="px-3 py-2.5 border-b border-gray-100 font-mono">{fmtShortTime(item.timestamp)}</td>
                                <td className="px-3 py-2.5 border-b border-gray-100 font-mono">{item.suite || 'n/a'}</td>
                                <td className="px-3 py-2.5 border-b border-gray-100 font-mono">{item.runner || 'n/a'}</td>
                                <td className="px-3 py-2.5 border-b border-gray-100">{item.total_cases ?? 0}</td>
                                <td className="px-3 py-2.5 border-b border-gray-100">{item.total_trials ?? 0}</td>
                                <td className={`px-3 py-2.5 border-b border-gray-100 font-mono font-bold ${scoreClass(item.overall_success_rate)}`}>{fmtPct(item.overall_success_rate)}</td>
                                <td className="px-3 py-2.5 border-b border-gray-100">{item.failed_cases ?? 0}</td>
                                <td className="px-3 py-2.5 border-b border-gray-100">{item.p0_failed_cases ?? 0}</td>
                                <td className="px-3 py-2.5 border-b border-gray-100">{Number(item.duration_seconds || 0).toFixed(1)}s</td>
                                <td className="px-3 py-2.5 border-b border-gray-100">
                                    <button className="px-3 py-1 border border-gray-300 bg-white rounded-md text-[12px] font-bold hover:bg-gray-50 transition" onClick={() => openReport(item.name)}>
                                        打开
                                    </button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );

    const renderCompare = () => (
        <div className="grid gap-4">
            <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                    <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">运行对比</h2>
                    <span className="font-mono text-[12px] text-gray-400">
                        {compareData ? `${compareData.baseline} -> ${compareData.candidate}` : '选择 baseline 和 candidate'}
                    </span>
                </div>
                <div className="flex gap-3 flex-wrap p-4 border-b border-gray-100 sticky top-[72px] z-[3] bg-white/95 backdrop-blur-sm">
                    <select
                        className="h-9 border border-gray-200 rounded-md px-2 text-[13px] min-w-[240px]"
                        value={baselineName}
                        onChange={(e) => setBaselineName(e.target.value)}
                    >
                        {reports.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
                    </select>
                    <select
                        className="h-9 border border-gray-200 rounded-md px-2 text-[13px] min-w-[240px]"
                        value={candidateName}
                        onChange={(e) => setCandidateName(e.target.value)}
                    >
                        {reports.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
                    </select>
                    <button
                        className="px-4 py-2 bg-gray-900 text-white rounded-md text-[13px] font-bold hover:bg-gray-800 transition"
                        onClick={handleCompare}
                        disabled={compareLoading}
                    >
                        {compareLoading ? '对比中...' : '对比'}
                    </button>
                </div>
                {compareData && (
                    <>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4">
                            {([
                                ['成功率变化', fmtDelta(compareData.summary_delta.overall_success_rate, true), compareData.summary_delta.overall_success_rate],
                                ['失败用例变化', fmtDelta(compareData.summary_delta.failed_cases), -compareData.summary_delta.failed_cases],
                                ['P0 失败变化', fmtDelta(compareData.summary_delta.p0_failed_cases), -compareData.summary_delta.p0_failed_cases],
                                ['耗时变化', `${fmtDelta(compareData.summary_delta.duration_seconds)}s`, -compareData.summary_delta.duration_seconds],
                            ] as const).map(([title, value, score]) => (
                                <div key={title} className="border border-gray-200 rounded-lg bg-white p-3 shadow-sm">
                                    <span className="text-[12px] font-extrabold text-gray-400 uppercase block">{title}</span>
                                    <strong className={`mt-2 block font-mono text-xl ${deltaSignClass(Number(score))}`}>{value}</strong>
                                </div>
                            ))}
                        </div>
                        {/* Delta breakdowns */}
                        {(['scene_delta', 'category_delta', 'metric_delta'] as const).map((key) => {
                            const entries = Object.entries(compareData[key] || {});
                            if (!entries.length) return null;
                            const kind = key === 'scene_delta' ? 'scene' : key === 'category_delta' ? 'category' : 'metric';
                            return (
                                <div key={key} className="px-4 pb-3 border-t border-gray-100 pt-3">
                                    <div className="font-extrabold text-[12px] uppercase text-gray-500 mb-2">
                                        {key === 'scene_delta' ? '场景变化' : key === 'category_delta' ? '类别变化' : '指标变化'}
                                    </div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {entries.slice(0, 12).map(([name, val]) => (
                                            <span key={name} className={`inline-flex px-2 py-0.5 border rounded-full text-[11px] font-mono ${Number(val.delta) >= 0 ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-red-50 text-red-700 border-red-200'}`}>
                                                {label(kind, name)} {fmtDelta(val.delta, true)}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            );
                        })}
                    </>
                )}
            </div>

            {/* Changes */}
            {compareData && (
                <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                    <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                        <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">变化明细</h2>
                        <span className="font-mono text-[12px] text-gray-400">
                            {(() => {
                                const c = compareData.case_changes;
                                return (c.regressions?.length || 0) + (c.fixes?.length || 0) + (c.score_drops?.length || 0) + (c.score_gains?.length || 0);
                            })()} 项
                        </span>
                    </div>
                    <div className="overflow-auto">
                        <table className="w-full border-collapse min-w-[1020px] text-[13px]">
                            <thead>
                                <tr>
                                    {['状态', '用例', '场景', '类别', '指标', 'Baseline', 'Candidate', 'Delta'].map((h) => (
                                        <th key={h} className="px-3 py-2.5 bg-gray-50 text-gray-500 text-[11px] uppercase text-left border-b border-gray-200">{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {(() => {
                                    const c = compareData.case_changes;
                                    const rows = [
                                        ...(c.regressions || []).map((r) => ({ ...r, title: '新增失败', kind: 'regression' as const })),
                                        ...(c.fixes || []).map((r) => ({ ...r, title: '已修复', kind: 'fix' as const })),
                                        ...(c.score_drops || []).map((r) => ({ ...r, title: '分数下降', kind: 'drop' as const })),
                                        ...(c.score_gains || []).map((r) => ({ ...r, title: '分数上升', kind: 'gain' as const })),
                                    ];
                                    if (!rows.length) return <tr><td colSpan={8} className="py-8 text-center text-gray-400">没有 case-level 变化</td></tr>;
                                    return rows.map((item, idx) => {
                                        const deltaValue = (item as any).delta ?? '';
                                        const deltaNumber = Number(deltaValue);
                                        return (
                                            <tr key={idx} className="hover:bg-gray-50 transition-colors">
                                                <td className="px-3 py-2.5 border-b border-gray-100">
                                                    <span className={`inline-flex px-2 py-0.5 border rounded-full text-[11px] font-bold ${item.kind === 'regression' || item.kind === 'drop' ? 'bg-red-50 text-red-700 border-red-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'}`}>
                                                        {item.title}
                                                    </span>
                                                </td>
                                                <td className="px-3 py-2.5 border-b border-gray-100 font-mono font-bold text-blue-600">{item.case_id || 'n/a'}</td>
                                                <td className="px-3 py-2.5 border-b border-gray-100">{label('scene', (item as any).scene)}</td>
                                                <td className="px-3 py-2.5 border-b border-gray-100">{label('category', (item as any).category)}</td>
                                                <td className="px-3 py-2.5 border-b border-gray-100 font-mono">{label('metric', (item as any).metric)}</td>
                                                <td className="px-3 py-2.5 border-b border-gray-100 font-mono">{(item as any).baseline ?? 'n/a'}</td>
                                                <td className="px-3 py-2.5 border-b border-gray-100 font-mono">{(item as any).candidate ?? 'n/a'}</td>
                                                <td className={`px-3 py-2.5 border-b border-gray-100 font-mono font-bold ${deltaSignClass(deltaNumber)}`}>
                                                    {deltaValue === '' ? 'n/a' : fmtDelta(deltaValue)}
                                                </td>
                                            </tr>
                                        );
                                    });
                                })()}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );

    const renderDetail = () => {
        const c = caseDetail?.case;
        const trial = (caseDetail?.trials || [])[0];
        return (
            <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                    <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">用例详情</h2>
                    <span className="font-mono text-[12px] text-gray-400">{c?.case_id || '未选择用例'}</span>
                </div>
                {!c ? (
                    <div className="p-6 text-center text-gray-400">从总览或失败分析中点击"查看"打开用例详情。</div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-[minmax(280px,360px)_minmax(0,1fr)]">
                        <div className="p-4 grid gap-3 border-r border-gray-100 bg-gray-50/50">
                            {([
                                ['用例', c.case_id, true],
                                ['场景', label('scene', c.scene)],
                                ['类别', label('category', c.category)],
                                ['优先级', label('priority', c.priority)],
                                ['任务', c.task],
                                ['成功率', fmtPct(c.success_rate), false, scoreClass(c.success_rate)],
                                ['实际场景', label('scene', trial?.actual_scene)],
                                ['执行器', trial?.actual_worker || 'n/a'],
                                ['失败类型', label('failureClass', trial?.failure_class)],
                                ['工具', (trial?.tool_calls || []).join(', ') || '无'],
                                ['阈值失败', (trial?.threshold_failures || []).map((f) => label('metric', f.metric)).join(', ') || '无'],
                                ['缺失指标', (trial?.missing_metrics || []).map((m) => label('metric', m)).join(', ') || '无'],
                                ['回答摘要', trial?.final_answer_preview || 'n/a'],
                            ] as const).map(([lbl, val, isMono, cls], i) => (
                                <div key={i} className="grid grid-cols-[84px_minmax(0,1fr)] gap-2 text-[13px] leading-relaxed">
                                    <span className="text-gray-400 font-extrabold">{lbl}</span>
                                    <span className={cls || (isMono ? 'font-mono' : '')}>{val}</span>
                                </div>
                            ))}
                        </div>
                        <div className="overflow-auto max-h-[70vh]">
                            <TraceTimeline events={trial?.trace_timeline || []} />
                        </div>
                    </div>
                )}
            </div>
        );
    };

    // ── Realtime eval ─────────────────────────────────────────────────────

    const loadRealtimeData = useCallback(async () => {
        setRealtimeLoading(true);
        try {
            const [recsRes, sumRes] = await Promise.all([
                appApi.evaluations.listRealtimeEvals({ limit: 100 }),
                appApi.evaluations.getRealtimeEvalSummary(realtimeHours),
            ]);
            setRealtimeRecords(recsRes.records || []);
            setRealtimeSummary(sumRes);
        } catch {
            // silently fail
        } finally {
            setRealtimeLoading(false);
        }
    }, [realtimeHours]);

    useEffect(() => {
        if (activeView === 'realtime') {
            loadRealtimeData();
            const timer = setInterval(loadRealtimeData, 30000); // auto-refresh every 30s
            return () => clearInterval(timer);
        }
    }, [activeView, loadRealtimeData]);

    const renderRealtime = () => {
        const sum = realtimeSummary;
        return (
            <div className="space-y-4">
                {/* Time range selector */}
                <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-gray-500">时间范围</span>
                    {[1, 6, 24, 72, 168].map(h => (
                        <button
                            key={h}
                            onClick={() => setRealtimeHours(h)}
                            className={`px-3 py-1 rounded text-xs font-bold border transition ${realtimeHours === h ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-200 text-gray-500 bg-white'}`}
                        >
                            {h < 24 ? `${h}h` : `${h / 24}d`}
                        </button>
                    ))}
                    <button
                        onClick={loadRealtimeData}
                        className="ml-auto px-3 py-1 rounded text-xs font-bold border border-gray-200 text-gray-500 bg-white hover:bg-gray-50"
                    >
                        ↻ 刷新
                    </button>
                </div>

                {realtimeLoading && !sum && <div className="text-center py-8 text-gray-400">加载中...</div>}

                {/* Summary cards */}
                {sum && (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <MetricCard title="对话总数" value={String(sum.total_evals)} hint={`最近 ${sum.hours}h`} />
                        <MetricCard title="平均质量" value={fmtPct(sum.avg_quality)} hint="overall_quality" />
                        <MetricCard title="Fallback 率" value={fmtPct(sum.fallback_rate)} hint="降级回答比例" />
                        <MetricCard title="无内容率" value={fmtPct(sum.no_content_rate)} hint="空推荐比例" />
                        <MetricCard title="泄露风险" value={fmtPct(sum.leak_rate)} hint="含敏感信息" />
                        <MetricCard title="平均效率" value={fmtPct(sum.avg_efficiency)} hint="步骤/重复/延迟" />
                        <MetricCard title="Schema 合规" value={fmtPct(sum.avg_schema_compliance)} hint="结构完整性" />
                        <MetricCard title="平均耗时" value={fmtMs(sum.avg_duration_ms)} hint="端到端" />
                    </div>
                )}

                {/* Quality trend */}
                {sum && sum.quality_trend && sum.quality_trend.length > 0 && (
                    <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-4">
                        <h3 className="text-sm font-bold text-gray-700 mb-3">质量趋势</h3>
                        <div className="h-[120px] flex items-end gap-[2px]">
                            {sum.quality_trend.map((pt, i) => {
                                const h = Math.max(2, (pt.avg_quality || 0) * 100);
                                return (
                                    <div key={i} className="flex-1 flex flex-col items-center justify-end" title={`${pt.hour}: quality=${(pt.avg_quality * 100).toFixed(1)}%, n=${pt.count}`}>
                                        <div className={`w-full rounded-t ${barColor(pt.avg_quality)}`} style={{ height: `${h}%` }} />
                                        <span className="text-[8px] text-gray-400 mt-1">{pt.hour?.slice(11, 16)}</span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* Scene distribution */}
                {sum && sum.scene_distribution && Object.keys(sum.scene_distribution).length > 0 && (
                    <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-4">
                        <h3 className="text-sm font-bold text-gray-700 mb-3">场景分布</h3>
                        {Object.entries(sum.scene_distribution).map(([scene, count]) => (
                            <BarRow key={scene} name={scene} rate={count / (sum.total_evals || 1)} kind="scene" />
                        ))}
                    </div>
                )}

                {/* Recent records table */}
                <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                    <h3 className="text-sm font-bold text-gray-700 px-4 pt-3 pb-2">最近对话评分</h3>
                    <div className="overflow-x-auto">
                        <table className="w-full text-[12px]">
                            <thead>
                                <tr className="border-b border-gray-100 text-gray-400 uppercase tracking-wide">
                                    <th className="px-3 py-2 text-left">时间</th>
                                    <th className="px-3 py-2 text-left">场景</th>
                                    <th className="px-3 py-2 text-right">质量</th>
                                    <th className="px-3 py-2 text-right">效率</th>
                                    <th className="px-3 py-2 text-right">Schema</th>
                                    <th className="px-3 py-2 text-center">Fallback</th>
                                    <th className="px-3 py-2 text-right">耗时</th>
                                    <th className="px-3 py-2 text-left">工具</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-50">
                                {realtimeRecords.slice(0, 50).map(r => (
                                    <tr key={r.id} className="hover:bg-gray-50">
                                        <td className="px-3 py-2 font-mono whitespace-nowrap">{r.timestamp?.replace('T', ' ').slice(5, 16) || '-'}</td>
                                        <td className="px-3 py-2">{label('scene', r.scene || '-')}</td>
                                        <td className={`px-3 py-2 text-right font-mono font-bold ${scoreClass(r.overall_quality)}`}>{fmtPct(r.overall_quality)}</td>
                                        <td className={`px-3 py-2 text-right font-mono ${scoreClass(r.efficiency)}`}>{fmtPct(r.efficiency)}</td>
                                        <td className={`px-3 py-2 text-right font-mono ${scoreClass(r.schema_compliance)}`}>{fmtPct(r.schema_compliance)}</td>
                                        <td className="px-3 py-2 text-center">
                                            {r.is_fallback
                                                ? <span className="inline-block px-2 py-0.5 rounded text-[10px] font-bold bg-red-50 text-red-600 border border-red-200">是</span>
                                                : <span className="text-gray-300">-</span>}
                                        </td>
                                        <td className="px-3 py-2 text-right font-mono">{fmtMs(r.total_duration_ms)}</td>
                                        <td className="px-3 py-2 text-gray-500 truncate max-w-[200px]">{(r.tool_names || []).join(', ') || '-'}</td>
                                    </tr>
                                ))}
                                {realtimeRecords.length === 0 && (
                                    <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">暂无实时评测数据</td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        );
    };

    const renderAnalysis = () => {
        const order = ['by_failure_class', 'by_error_reason', 'by_metric', 'by_tool', 'by_worker', 'by_scene', 'by_category', 'by_case'];
        const groups = Object.entries(failureSummary)
            .filter(([, v]) => v && Object.keys(v).length)
            .sort(([a], [b]) => {
                const ai = order.indexOf(a);
                const bi = order.indexOf(b);
                return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
            });

        return (
            <div className="grid gap-4">
                {/* Failure path guide */}
                <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                    <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                        <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">失败定位路径</h2>
                        <span className="font-mono text-[12px] text-gray-400">failure class -&gt; case -&gt; trace</span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 p-4">
                        {['先按失败类型判断 provider、工具/API、agent 质量或评测框架问题。', '再按 metric、tool、worker 缩小到具体能力或执行器。', '最后打开用例详情，沿 trace 时间线定位出错步骤。'].map((text, i) => (
                            <div key={i} className="grid grid-cols-[28px_minmax(0,1fr)] gap-2 items-start p-3 border border-gray-200 rounded-lg bg-white">
                                <strong className="w-6 h-6 rounded-full bg-gray-900 text-white font-mono text-[12px] flex items-center justify-center">{i + 1}</strong>
                                <span className="text-gray-500 text-[13px] leading-relaxed">{text}</span>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Analysis grid */}
                <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
                    <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
                        <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">失败分析</h2>
                        <span className="font-mono text-[12px] text-gray-400">{groups.length} 个聚合维度</span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4">
                        {groups.length === 0 && <div className="col-span-full py-6 text-center text-gray-400">当前报告没有失败聚合</div>}
                        {groups.map(([group, value]) => {
                            const entries = Object.entries(value || {}).sort(([, a], [, b]) => {
                                const av = typeof a === 'object' ? Number((a as { success_rate?: number }).success_rate ?? 0) : Number(a ?? 0);
                                const bv = typeof b === 'object' ? Number((b as { success_rate?: number }).success_rate ?? 0) : Number(b ?? 0);
                                return bv - av;
                            });
                            return (
                                <div key={group} className="border border-gray-200 rounded-lg overflow-hidden">
                                    <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-gray-100">
                                        <h2 className="text-[12px] font-extrabold uppercase text-gray-500 m-0">{label('failureGroup', group)}</h2>
                                        <span className="font-mono text-[12px] text-gray-400">{entries.length}</span>
                                    </div>
                                    <div className="p-3 flex flex-wrap gap-1.5">
                                        {entries.map(([name, count]) => {
                                            const displayLabel = group === 'by_failure_class' ? label('failureClass', name) : name;
                                            const displayValue = typeof count === 'object' ? fmtPct((count as { success_rate?: number }).success_rate) : String(count);
                                            return (
                                                <button
                                                    key={name}
                                                    className="px-2.5 py-1 border border-gray-300 bg-white rounded-md text-[12px] font-bold hover:bg-gray-50 transition"
                                                    onClick={() => {
                                                        if (group === 'by_scene') setSceneFilter(name);
                                                        else if (group === 'by_category') setCategoryFilter(name);
                                                        else setSearchQuery(name);
                                                        setActiveView('overview');
                                                    }}
                                                >
                                                    {displayLabel} {displayValue}
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        );
    };

    const VIEW_RENDERERS: Record<View, () => React.ReactNode> = {
        overview: renderOverview,
        history: renderHistory,
        compare: renderCompare,
        detail: renderDetail,
        analysis: renderAnalysis,
        realtime: renderRealtime,
    };

    // ---------------------------------------------------------------------------
    // Main render
    // ---------------------------------------------------------------------------

    return (
        <EvaluationAccessGate>
            <div className="min-h-full bg-gray-100 text-gray-900" style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Microsoft YaHei", sans-serif' }}>
                {/* Header */}
                <header className="sticky top-0 z-10 flex items-center justify-between gap-4 px-4 md:px-6 py-3 bg-gradient-to-r from-gray-900 to-gray-800 shadow-lg">
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                        <div className="w-9 h-9 rounded-lg bg-white flex items-center justify-center font-mono font-bold text-[14px] border border-white/20 shadow-inner">EV</div>
                        <div className="min-w-0">
                            <h1 className="m-0 text-[17px] font-bold text-gray-50 leading-tight">Smart Eats 评测控制台</h1>
                            <div className="text-gray-400 font-mono text-[12px] truncate max-w-[58vw]">
                                {loading ? '正在加载...' : `${source} / ${selectedReportName}`}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 flex-wrap justify-end">
                        <span className={`inline-flex items-center px-2 py-0.5 border rounded-full text-[11px] font-bold ${statusColor(report?.overall_success_rate)}`}>
                            success {fmtPct(report?.overall_success_rate)}
                        </span>
                        <select
                            className="h-9 border border-white/20 bg-white/95 rounded-md px-2 text-[13px] w-[min(240px,28vw)]"
                            value={selectedReportName}
                            onChange={(e) => loadReport(e.target.value)}
                        >
                            {reports.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
                        </select>
                        <select
                            className="h-9 border border-white/20 bg-white/95 rounded-md px-2 text-[13px] w-28"
                            value={sceneFilter}
                            onChange={(e) => setSceneFilter(e.target.value)}
                        >
                            <option value="">全部场景</option>
                            {scenes.map((s) => <option key={s} value={s}>{label('scene', s)}</option>)}
                        </select>
                        <select
                            className="h-9 border border-white/20 bg-white/95 rounded-md px-2 text-[13px] w-28"
                            value={categoryFilter}
                            onChange={(e) => setCategoryFilter(e.target.value)}
                        >
                            <option value="">全部类别</option>
                            {categories.map((c) => <option key={c} value={c}>{label('category', c)}</option>)}
                        </select>
                        <input
                            type="search"
                            placeholder="用例 ID / 任务 / 错误"
                            className="h-9 border border-white/20 bg-white/95 rounded-md px-2 text-[13px] w-44"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                        />
                        <button
                            className="h-9 px-3 bg-gray-900 text-white border border-white/20 rounded-md text-[13px] font-bold hover:bg-gray-700 transition"
                            onClick={() => { loadReport(selectedReportName).then(() => loadReportList()); }}
                        >
                            刷新
                        </button>
                        <button
                            className="h-9 px-3 bg-white text-gray-900 border border-white/20 rounded-md text-[13px] font-bold hover:bg-gray-100 transition"
                            onClick={() => setRawOpen(!rawOpen)}
                        >
                            原始 JSON
                        </button>
                    </div>
                </header>

                {/* Body */}
                <div className="max-w-[1560px] mx-auto grid grid-cols-1 md:grid-cols-[170px_minmax(0,1fr)] gap-4 p-4 md:p-6 pb-12">
                    {/* Sidebar */}
                    <aside className="hidden md:grid sticky top-[72px] self-start gap-2 p-3 border border-gray-200 rounded-lg bg-white/90 shadow-sm">
                        <div className="px-1.5 pb-2 text-gray-400 font-mono text-[11px] font-extrabold uppercase border-b border-gray-200">工作台</div>
                        <nav className="grid gap-1.5">
                            {TAB_ITEMS.map((tab) => (
                                <button
                                    key={tab.key}
                                    className={`h-9 border rounded-md text-[13px] font-bold text-left px-3 transition ${activeView === tab.key ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-200 text-gray-600 hover:bg-white hover:-translate-y-px'}`}
                                    onClick={() => setActiveView(tab.key)}
                                >
                                    {tab.label}
                                </button>
                            ))}
                        </nav>
                    </aside>

                    {/* Mobile tabs */}
                    <div className="md:hidden flex flex-wrap gap-2">
                        {TAB_ITEMS.map((tab) => (
                            <button
                                key={tab.key}
                                className={`h-9 border rounded-md text-[13px] font-bold px-3 transition ${activeView === tab.key ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-200 text-gray-600 bg-white/80 shadow-sm'}`}
                                onClick={() => setActiveView(tab.key)}
                            >
                                {tab.label}
                            </button>
                        ))}
                    </div>

                    {/* Workspace */}
                    <main className="min-w-0 grid gap-4">
                        {/* Status line */}
                        <div className="flex items-center justify-between gap-3 px-3 py-2.5 border border-gray-200 rounded-lg bg-white/80 font-mono text-[12px] text-gray-500 shadow-sm">
                            <span>{error ? `错误：${error}` : loading ? '正在加载报告...' : `报告时间 ${report?.timestamp || 'n/a'}`}</span>
                            <span>当前显示 {filteredResults.length} 条用例</span>
                        </div>

                        {/* View content */}
                        {VIEW_RENDERERS[activeView]()}
                    </main>
                </div>

                {/* Raw JSON drawer */}
                {rawOpen && (
                    <div className="fixed inset-x-0 bottom-0 max-h-[62vh] overflow-auto bg-gray-900 text-gray-200 border-t border-gray-700 shadow-2xl z-20">
                        <div className="sticky top-0 flex justify-between items-center gap-3 px-4 py-3 bg-gray-900 border-b border-gray-700">
                            <strong className="text-gray-200">{selectedReportName}</strong>
                            <button
                                className="px-3 py-1 bg-white text-gray-900 border border-gray-300 rounded-md text-[12px] font-bold"
                                onClick={() => setRawOpen(false)}
                            >
                                关闭
                            </button>
                        </div>
                        <pre className="m-0 p-4 whitespace-pre-wrap font-mono text-[12px] leading-relaxed">
                            {JSON.stringify(report || {}, null, 2)}
                        </pre>
                    </div>
                )}
            </div>
        </EvaluationAccessGate>
    );
};

const EvaluationWorkbench: React.FC = () => <EvaluationWorkbenchInner />;

export default EvaluationWorkbench;
