import React, { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, ChevronRight, Clock3, ShieldCheck, WalletCards } from 'lucide-react';
import { Header, ScreenScroll } from '../components/Layout';
import { appApi, ApiError } from '../services/api';

type Tab = 'overview' | 'sessions' | 'failures' | 'cost' | 'safety' | 'reviews' | 'offline';
type Tone = 'good' | 'warn' | 'bad' | 'info' | 'neutral';

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'overview', label: '总览' },
  { key: 'sessions', label: '会话' },
  { key: 'failures', label: '问题' },
  { key: 'cost', label: '成本' },
  { key: 'safety', label: '安全' },
  { key: 'reviews', label: '审核' },
  { key: 'offline', label: '离线' },
];

const FAILURE_COPY: Record<string, { title: string; action: string; tone: Tone }> = {
  provider_auth: { title: '模型鉴权失败', action: '检查 API Key 和账号权限。', tone: 'bad' },
  provider_timeout: { title: '模型响应超时', action: '检查模型服务或降低任务复杂度。', tone: 'warn' },
  provider_rate_limit: { title: '模型限流', action: '降低并发或切换模型。', tone: 'warn' },
  provider_model_error: { title: '模型服务异常', action: '检查模型名和 provider 状态。', tone: 'bad' },
  tool_api_error: { title: '工具接口失败', action: '检查外部 API、网络和参数。', tone: 'bad' },
  tool_timeout: { title: '工具调用超时', action: '检查工具延迟和降级策略。', tone: 'warn' },
  tool_empty_result: { title: '工具无有效结果', action: '检查查询条件和数据覆盖。', tone: 'warn' },
  tool_bad_args: { title: '工具参数错误', action: '检查 Agent 工具参数生成。', tone: 'bad' },
  agent_routing_error: { title: 'Agent 路由错误', action: '补充路由样例和规则。', tone: 'bad' },
  agent_schema_error: { title: '回答结构错误', action: '检查结构化输出和兜底格式。', tone: 'bad' },
  agent_low_quality: { title: '回答质量偏低', action: '复核用户目标和最终答案。', tone: 'warn' },
  safety_policy_violation: { title: '安全合规风险', action: '优先人工审核。', tone: 'bad' },
  eval_framework_error: { title: '评测系统异常', action: '检查评测任务和指标。', tone: 'warn' },
  none: { title: '未发现明确失败', action: '无需处理。', tone: 'good' },
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

function toolLabel(name?: string | null) {
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
}

function summarizeTools(tools: any[]) {
  const grouped = new Map<string, { name: string; count: number; failed: number; latency: number[]; cost: number }>();
  for (const tool of tools || []) {
    const name = String(tool.tool_name || tool.name || 'unknown');
    const row = grouped.get(name) || { name, count: 0, failed: 0, latency: [], cost: 0 };
    row.count += 1;
    if (tool.success === false) row.failed += 1;
    const latency = Number(tool.latency_ms);
    if (Number.isFinite(latency) && latency > 0) row.latency.push(latency);
    row.cost += Number(tool.cost || 0);
    grouped.set(name, row);
  }
  return Array.from(grouped.values());
}

export function EvalWorkbenchScreen({ onBack }: { onBack: () => void }) {
  const [tab, setTab] = useState<Tab>('overview');
  const [expertMode, setExpertMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [overview, setOverview] = useState<any>(null);
  const [records, setRecords] = useState<any[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<any>(null);
  const [failures, setFailures] = useState<any>(null);
  const [cost, setCost] = useState<any>(null);
  const [safety, setSafety] = useState<any>(null);
  const [reviews, setReviews] = useState<any>(null);
  const [reports, setReports] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);

  const loadAll = async () => {
    setLoading(true);
    setError('');
    try {
      const [overviewData, traceData, failureData, costData, safetyData, reviewData, reportData, jobData, datasetData] = await Promise.all([
        appApi.evaluations.getMonitoringOverview('24h'),
        appApi.evaluations.listMonitoringTraces({ limit: 80 }),
        appApi.evaluations.getMonitoringFailures('24h'),
        appApi.evaluations.getMonitoringCostLatency('24h'),
        appApi.evaluations.getMonitoringSafety('24h'),
        appApi.evaluations.listMonitoringReviews({ decision: 'pending', limit: 50 }),
        appApi.evaluations.listReports(),
        appApi.evaluations.listEvalJobs({ limit: 20 }),
        appApi.evaluations.listDatasets(),
      ]);
      setOverview(overviewData);
      setRecords(traceData.records || []);
      setFailures(failureData);
      setCost(costData);
      setSafety(safetyData);
      setReviews(reviewData);
      setReports(reportData.reports || []);
      setJobs(jobData.records || []);
      setDatasets(datasetData.datasets || []);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载评测数据失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
    const timer = setInterval(loadAll, 30000);
    return () => clearInterval(timer);
  }, []);

  const quality = useMemo(() => buildQualityView(overview, reviews), [overview, reviews]);
  const sessions = useMemo(() => groupSessions(records), [records]);
  const failureItems = useMemo(() => buildFailureItems(failures), [failures]);

  const openTrace = async (runId: string) => {
    try {
      const detail = await appApi.evaluations.getMonitoringTrace(runId);
      setSelectedTrace(detail);
      setTab('sessions');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载执行过程失败');
    }
  };

  return (
    <>
      <Header title="质检运营台" subtitle="AgentEval Hub / Quality Ops" onBack={onBack} />

      <div className="border-b border-gray-100 bg-white px-4 py-3">
        <div className="grid grid-cols-2 rounded-xl bg-gray-100 p-1">
          <button onClick={() => setExpertMode(false)} className={`rounded-lg py-2 text-xs font-black ${!expertMode ? 'bg-white text-gray-950 shadow-sm' : 'text-gray-500'}`}>业务视图</button>
          <button onClick={() => setExpertMode(true)} className={`rounded-lg py-2 text-xs font-black ${expertMode ? 'bg-white text-gray-950 shadow-sm' : 'text-gray-500'}`}>专家视图</button>
        </div>
      </div>

      <div className="flex overflow-x-auto border-b border-gray-200 bg-white px-2">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`min-w-[72px] flex-1 py-3 text-xs font-black ${
              tab === key ? 'border-b-2 border-gray-950 text-gray-950' : 'text-gray-400'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <ScreenScroll>
        {loading && (
          <div className="flex items-center justify-center py-12">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-900" />
          </div>
        )}
        {error && <div className="mx-4 mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm font-bold text-red-600">{error}</div>}
        {!loading && !error && tab === 'overview' && <OverviewTab quality={quality} records={records} failures={failureItems} onGo={setTab} />}
        {!loading && !error && tab === 'sessions' && <SessionsTab sessions={sessions} selectedTrace={selectedTrace} expertMode={expertMode} onOpenTrace={openTrace} />}
        {!loading && !error && tab === 'failures' && <FailuresTab items={failureItems} records={records} onOpenTrace={openTrace} />}
        {!loading && !error && tab === 'cost' && <CostTab data={cost} />}
        {!loading && !error && tab === 'safety' && <SafetyTab data={safety} />}
        {!loading && !error && tab === 'reviews' && <ReviewsTab data={reviews} onRefresh={loadAll} onOpenTrace={openTrace} />}
        {!loading && !error && tab === 'offline' && <OfflineTab reports={reports} jobs={jobs} datasets={datasets} />}
      </ScreenScroll>
    </>
  );
}

function buildQualityView(overview: any, reviews: any) {
  return {
    total: overview?.total_runs || 0,
    success: overview?.task_success_proxy || 0,
    toolError: overview?.tool_error_rate || 0,
    latencyP95: overview?.latency_p95_ms || 0,
    cost: overview?.total_cost || overview?.token_cost || 0,
    pending: reviews?.total || reviews?.records?.length || 0,
    safetyRisk: Math.max(Number(overview?.secret_leak_rate || 0), Number(overview?.policy_violation_rate || 0)),
  };
}

function groupSessions(records: any[]) {
  const map = new Map<string, any[]>();
  records.forEach((record) => {
    const sid = record.session_id || record.id;
    map.set(sid, [...(map.get(sid) || []), record]);
  });
  return Array.from(map.entries()).map(([sessionId, runs]) => ({
    sessionId,
    latest: runs[0],
    runs,
  }));
}

function buildFailureItems(failures: any) {
  const byClass = failures?.by_failure_class || {};
  return Object.entries(byClass)
    .filter(([, count]) => Number(count) > 0)
    .map(([key, count]) => ({ key, count: Number(count), ...(FAILURE_COPY[key] || { title: key, action: '查看执行过程定位原因。', tone: 'warn' as Tone }) }));
}

function OverviewTab({ quality, records, failures, onGo }: { quality: any; records: any[]; failures: any[]; onGo: (tab: Tab) => void }) {
  return (
    <div className="space-y-4 px-4 pb-8">
      <Conclusion title={quality.success >= 0.8 ? '整体质量稳定' : '质量需要关注'} body={quality.pending ? '优先处理待人工审核会话。' : '继续观察低分、工具失败和成本异常。'} tone={quality.success >= 0.8 ? 'good' : 'warn'} />
      <div className="grid grid-cols-2 gap-2">
        <Kpi label="对话数" value={quality.total} />
        <Kpi label="完成率" value={pct(quality.success)} tone={scoreTone(quality.success)} />
        <Kpi label="工具失败" value={pct(quality.toolError)} tone={quality.toolError > 0 ? 'bad' : 'good'} />
        <Kpi label="待审核" value={quality.pending} tone={quality.pending ? 'warn' : 'good'} />
        <Kpi label="P95 耗时" value={ms(quality.latencyP95)} />
        <Kpi label="成本" value={money(quality.cost)} />
      </div>
      <div className="grid grid-cols-3 gap-2">
        <ActionCard title="看会话" body="逐条质检真实对话" onClick={() => onGo('sessions')} />
        <ActionCard title="看问题" body="按原因归类处理" onClick={() => onGo('failures')} />
        <ActionCard title="看审核" body="处理待复核项" onClick={() => onGo('reviews')} />
      </div>
      <Section title="最近会话">
        {records.slice(0, 6).map((item) => <RunCard key={item.id} run={item} />)}
        {!records.length && <Empty title="还没有会话数据" body="开启在线评测并完成一轮聊天后，这里会出现质检结果。" />}
      </Section>
      <Section title="主要问题">
        {failures.slice(0, 3).map((item) => <FailureCard key={item.key} item={item} />)}
        {!failures.length && <Empty title="没有明显问题" body="当前窗口内没有模型、工具或安全问题。" />}
      </Section>
    </div>
  );
}

function SessionsTab({ sessions, selectedTrace, expertMode, onOpenTrace }: { sessions: any[]; selectedTrace: any; expertMode: boolean; onOpenTrace: (runId: string) => void }) {
  return (
    <div className="space-y-4 px-4 pb-8">
      <Section title="会话质检">
        {sessions.map((session) => {
          const run = session.latest || {};
          return (
            <button key={session.sessionId} onClick={() => onOpenTrace(run.id)} className="w-full rounded-xl bg-white p-4 text-left shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-xs font-black text-blue-700">{session.sessionId}</span>
                <Badge tone={scoreTone(run.overall_quality)}>{pct(run.overall_quality)}</Badge>
              </div>
              <div className="mt-2 text-sm font-black text-gray-900">{sceneLabel(run.scene)} · {run.status === 'completed' ? '已完成' : '异常'}</div>
              <div className="mt-1 text-xs font-semibold text-gray-500">{session.runs.length} 轮 · {ms(run.latency_ms || run.total_duration_ms)} · {run.model_name || '未知模型'}</div>
            </button>
          );
        })}
        {!sessions.length && <Empty title="暂无会话" body="完成一轮线上对话后，会按 session 分组展示。" />}
      </Section>
      <TraceDetailMobile detail={selectedTrace} expertMode={expertMode} />
    </div>
  );
}

function TraceDetailMobile({ detail, expertMode }: { detail: any; expertMode: boolean }) {
  if (!detail) return <Empty title="选择一条会话" body="这里会展示这轮 Agent 的执行过程、工具调用、成本和开发者详情。" />;
  const run = detail.run || {};
  const events = detail.spans?.length ? detail.spans : detail.events || [];
  const tools = detail.tool_calls || [];
  const toolSummaries = summarizeTools(tools);
  return (
    <Section title="执行过程">
      <Conclusion title={run.status === 'completed' ? '这轮执行完成了' : '这轮执行异常'} body={`${sceneLabel(run.scene)} · ${run.model_name || '未知模型'} · ${ms(run.latency_ms)}`} tone={scoreTone(run.overall_quality)} />
      <div className="space-y-2">
        {events.map((event: any, index: number) => (
          <div key={event.id || index} className="rounded-xl border border-gray-100 bg-white p-3">
            <div className="flex items-center gap-2">
              <span className="grid h-7 w-7 place-items-center rounded-full bg-gray-900 text-xs font-black text-white">{index + 1}</span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-black text-gray-900">{spanTitle(event.span_type || event.event_type)}</div>
                <div className="truncate text-xs font-semibold text-gray-500">{event.name || event.tool_name || event.event_type || event.span_type}</div>
              </div>
              <span className="text-xs font-bold text-gray-400">{ms(event.duration_ms)}</span>
            </div>
          </div>
        ))}
        {!events.length && <Empty title="暂无步骤" body="旧数据可能没有 span；新会话会生成可读时间线。" />}
      </div>
      {tools.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-black text-gray-500">工具调用</div>
          {toolSummaries.map((tool: any) => {
            const avgLatency = tool.latency.length ? tool.latency.reduce((sum: number, item: number) => sum + item, 0) / tool.latency.length : null;
            return (
            <div key={tool.name} className="rounded-xl bg-gray-50 p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-black">{toolLabel(tool.name)}</span>
                <Badge tone={tool.failed ? 'bad' : 'good'}>{tool.failed ? `失败 ${tool.failed}` : '全部成功'}</Badge>
              </div>
              <div className="mt-1 text-xs font-semibold text-gray-500">调用 {tool.count} 次 · {toolMs(avgLatency)} · {money(tool.cost)}</div>
              <div className="mt-1 font-mono text-[10px] font-semibold text-gray-400">{tool.name}</div>
            </div>
            );
          })}
        </div>
      )}
      {expertMode && <RawJson value={detail} />}
    </Section>
  );
}

function FailuresTab({ items, records, onOpenTrace }: { items: any[]; records: any[]; onOpenTrace: (runId: string) => void }) {
  return (
    <div className="space-y-4 px-4 pb-8">
      <Section title="问题归因">
        {items.map((item) => <FailureCard key={item.key} item={item} />)}
        {!items.length && <Empty title="没有明显问题" body="当前窗口内没有可聚合的失败类型。" />}
      </Section>
      <Section title="需要复核的执行">
        {records.filter((run) => run.status !== 'completed' || Number(run.overall_quality || 1) < 0.8).slice(0, 8).map((run) => (
          <button key={run.id} onClick={() => onOpenTrace(run.id)} className="w-full rounded-xl bg-white p-3 text-left shadow-sm">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs font-black text-blue-700">{run.id}</span>
              <Badge tone={scoreTone(run.overall_quality)}>{pct(run.overall_quality)}</Badge>
            </div>
            <div className="mt-1 text-xs font-semibold text-gray-500">{sceneLabel(run.scene)} · {run.status}</div>
          </button>
        ))}
      </Section>
    </div>
  );
}

function CostTab({ data }: { data: any }) {
  return (
    <div className="space-y-4 px-4 pb-8">
      <div className="grid grid-cols-2 gap-2">
        <Kpi label="运行数" value={data?.total_runs || 0} />
        <Kpi label="总成本" value={money(data?.total_cost)} />
        <Kpi label="P50 耗时" value={ms(data?.latency_p50_ms)} />
        <Kpi label="P95 耗时" value={ms(data?.latency_p95_ms)} />
        <Kpi label="输入 Token" value={data?.token_input || 0} />
        <Kpi label="输出 Token" value={data?.token_output || 0} />
      </div>
      <Conclusion title="成本怎么看" body="优先看总成本和 P95 耗时；如果成本突然升高，再看模型和工具调用分布。" tone="info" />
    </div>
  );
}

function SafetyTab({ data }: { data: any }) {
  return (
    <div className="space-y-4 px-4 pb-8">
      <div className="grid grid-cols-2 gap-2">
        <Kpi label="运行数" value={data?.total_runs || 0} />
        <Kpi label="无泄露" value={pct(data?.no_leak ?? (1 - Number(data?.secret_leak_rate || 0)))} />
        <Kpi label="敏感泄露" value={pct(data?.secret_leak_rate)} tone={Number(data?.secret_leak_rate || 0) > 0 ? 'bad' : 'good'} />
        <Kpi label="策略违规" value={pct(data?.policy_violation_rate)} tone={Number(data?.policy_violation_rate || 0) > 0 ? 'bad' : 'good'} />
      </div>
      <Conclusion title="安全优先级" body="只要出现泄露或策略违规，就应优先进入人工审核，不看平均分。" tone={Number(data?.secret_leak_rate || 0) > 0 || Number(data?.policy_violation_rate || 0) > 0 ? 'bad' : 'good'} />
    </div>
  );
}

function ReviewsTab({ data, onRefresh, onOpenTrace }: { data: any; onRefresh: () => void; onOpenTrace: (runId: string) => void }) {
  const records = data?.records || [];
  return (
    <div className="space-y-3 px-4 pb-8">
      {records.map((item: any) => {
        const run = item.run || {};
        return (
          <div key={run.id} className="rounded-xl bg-white p-4 shadow-sm">
            <div className="font-mono text-xs font-black text-blue-700">{run.id}</div>
            <div className="mt-2 text-sm font-black text-gray-900">进入审核：{displayValue(item.review?.failure_reason || item.review?.reason || run.failure_class, '低分或风险命中')}</div>
            <div className="mt-1 text-xs font-semibold text-gray-500">{sceneLabel(run.scene)} · {run.status}</div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button onClick={() => appApi.evaluations.submitMonitoringReview(run.id, { decision: 'accepted' }).then(onRefresh)} className="rounded-full bg-emerald-50 py-2 text-xs font-black text-emerald-700">接受</button>
              <button onClick={() => appApi.evaluations.submitMonitoringReview(run.id, { decision: 'rejected', reason: 'manual_reject' }).then(onRefresh)} className="rounded-full bg-red-50 py-2 text-xs font-black text-red-600">拒绝</button>
              <button onClick={() => appApi.evaluations.submitMonitoringReview(run.id, { decision: 'converted_to_case', reason: 'converted_to_case' }).then(onRefresh)} className="rounded-full bg-blue-50 py-2 text-xs font-black text-blue-700">转 Case</button>
              <button onClick={() => onOpenTrace(run.id)} className="rounded-full bg-gray-100 py-2 text-xs font-black text-gray-700">看过程</button>
            </div>
          </div>
        );
      })}
      {!records.length && <Empty title="暂无待审核" body="低分、工具失败、安全风险或人工标记的会话会出现在这里。" />}
    </div>
  );
}

function OfflineTab({ reports, jobs, datasets }: { reports: any[]; jobs: any[]; datasets: any[] }) {
  return (
    <div className="space-y-4 px-4 pb-8">
      <div className="grid grid-cols-2 gap-2">
        <Kpi label="离线报告" value={reports.length} />
        <Kpi label="评测任务" value={jobs.length} />
        <Kpi label="数据集" value={datasets.length} />
        <Kpi label="最新通过率" value={reports[0] ? pct(reports[0].overall_success_rate) : 'n/a'} />
      </div>
      <Section title="最近报告">
        {reports.slice(0, 8).map((report) => (
          <div key={report.name} className="rounded-xl bg-white p-3 shadow-sm">
            <div className="truncate font-mono text-xs font-black text-blue-700">{report.name}</div>
            <div className="mt-1 text-xs font-semibold text-gray-500">{report.suite || 'n/a'} / {report.runner || 'n/a'} · 失败 {report.failed_cases || 0}</div>
          </div>
        ))}
        {!reports.length && <Empty title="暂无离线报告" body="运行 quick/full 评测后会在这里展示。" />}
      </Section>
    </div>
  );
}

function RunCard({ run }: { run: any }) {
  return (
    <div className="rounded-xl bg-white p-3 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-xs font-black text-blue-700">{run.id}</span>
        <Badge tone={scoreTone(run.overall_quality)}>{pct(run.overall_quality)}</Badge>
      </div>
      <div className="mt-1 text-xs font-semibold text-gray-500">{sceneLabel(run.scene)} · {run.status || 'completed'} · {ms(run.latency_ms || run.total_duration_ms)}</div>
    </div>
  );
}

function FailureCard({ item }: { item: any }) {
  return (
    <div className="rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-black text-gray-900">{item.title}</div>
        <Badge tone={item.tone}>{item.count}</Badge>
      </div>
      <div className="mt-2 text-xs font-semibold leading-relaxed text-gray-500">{item.action}</div>
    </div>
  );
}

function ActionCard({ title, body, onClick }: { title: string; body: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="rounded-xl bg-white p-3 text-left shadow-sm">
      <div className="flex items-center justify-between">
        <span className="text-sm font-black text-gray-900">{title}</span>
        <ChevronRight size={14} className="text-gray-400" />
      </div>
      <div className="mt-1 text-[11px] font-semibold leading-relaxed text-gray-500">{body}</div>
    </button>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-black text-gray-500">{title}</h3>
      {children}
    </section>
  );
}

function Conclusion({ title, body, tone = 'neutral' }: { title: string; body: string; tone?: Tone }) {
  const Icon = tone === 'good' ? CheckCircle2 : tone === 'bad' ? AlertTriangle : tone === 'warn' ? AlertTriangle : Activity;
  return (
    <div className={`rounded-xl border p-4 ${toneClass(tone)}`}>
      <div className="flex gap-3">
        <Icon size={20} className="mt-0.5" />
        <div>
          <div className="font-black">{title}</div>
          <div className="mt-1 text-sm font-semibold leading-relaxed opacity-90">{body}</div>
        </div>
      </div>
    </div>
  );
}

function Kpi({ label, value, tone = 'neutral' }: { label: string; value: React.ReactNode; tone?: Tone }) {
  return (
    <div className="rounded-xl bg-white p-3 shadow-sm">
      <div className="text-[11px] font-black text-gray-400">{label}</div>
      <div className={`mt-1 text-xl font-black ${tone === 'good' ? 'text-emerald-600' : tone === 'warn' ? 'text-amber-600' : tone === 'bad' ? 'text-red-600' : 'text-gray-950'}`}>{value}</div>
    </div>
  );
}

function Badge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: Tone }) {
  return <span className={`rounded-full px-2 py-1 text-[10px] font-black ${toneClass(tone)}`}>{children}</span>;
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center">
      <div className="text-sm font-black text-gray-700">{title}</div>
      <div className="mx-auto mt-1 max-w-[260px] text-xs font-semibold leading-relaxed text-gray-400">{body}</div>
    </div>
  );
}

function RawJson({ value }: { value: any }) {
  return (
    <details className="rounded-xl bg-gray-950 p-3 text-gray-100">
      <summary className="cursor-pointer text-xs font-black">开发者详情 Raw JSON</summary>
      <pre className="mt-3 max-h-[320px] overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed">{JSON.stringify(value || {}, null, 2)}</pre>
    </details>
  );
}

function spanTitle(type?: string) {
  const map: Record<string, string> = {
    llm_call: '调用模型理解和生成',
    tool_call: '调用外部工具',
    router: '判断场景和执行器',
    executor: '执行并生成结果',
    guardrail: '安全检查或异常处理',
    model_usage: '记录模型 token 用量',
    final: '返回最终答案',
    error: '发生异常',
  };
  return map[type || ''] || type || '执行步骤';
}

function sceneLabel(value?: string) {
  const map: Record<string, string> = {
    home_chef: '在家做饭',
    cook_home: '在家做饭',
    eat_out: '出去吃',
    travel_planner: '旅行规划',
    route: '路线导航',
    chat: '通用聊天',
  };
  return map[value || ''] || value || '未知场景';
}

function toneClass(tone: Tone) {
  if (tone === 'good') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (tone === 'warn') return 'border-amber-200 bg-amber-50 text-amber-700';
  if (tone === 'bad') return 'border-red-200 bg-red-50 text-red-700';
  if (tone === 'info') return 'border-blue-200 bg-blue-50 text-blue-700';
  return 'border-gray-200 bg-gray-50 text-gray-600';
}

function scoreTone(value: any): Tone {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'neutral';
  if (n >= 0.8) return 'good';
  if (n >= 0.5) return 'warn';
  return 'bad';
}

function pct(value: any) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : 'n/a';
}

function ms(value: any) {
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n)}ms` : 'n/a';
}

function toolMs(value: any) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? `${Math.round(n)}ms` : '耗时未记录';
}

function money(value: any) {
  const n = Number(value || 0);
  return `$${n.toFixed(n >= 10 ? 1 : 3)}`;
}
