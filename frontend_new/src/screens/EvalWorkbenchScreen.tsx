import React, { useEffect, useState } from 'react';
import { Header, ScreenScroll } from '../components/Layout';
import { appApi, ApiError } from '../services/api';

type Tab = 'realtime' | 'overview' | 'history' | 'compare' | 'case' | 'failures';

export function EvalWorkbenchScreen({ onBack }: { onBack: () => void }) {
  const [tab, setTab] = useState<Tab>('realtime');
  const [reports, setReports] = useState<any[]>([]);
  const [selectedReport, setSelectedReport] = useState<string>('latest.json');
  const [reportData, setReportData] = useState<any>(null);
  const [caseData, setCaseData] = useState<any>(null);
  const [selectedCaseId, setSelectedCaseId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Realtime eval state
  const [realtimeRecords, setRealtimeRecords] = useState<any[]>([]);
  const [realtimeSummary, setRealtimeSummary] = useState<any>(null);
  const [realtimeHours, setRealtimeHours] = useState(24);

  useEffect(() => {
    loadReports();
  }, []);

  const loadReports = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await appApi.evaluations.listReports();
      setReports(data.reports || []);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载报告失败');
    } finally {
      setLoading(false);
    }
  };

  const loadReport = async (name: string) => {
    setLoading(true);
    setError('');
    try {
      const data = await appApi.evaluations.getReport(name);
      setReportData(data.report || data);
      setSelectedReport(name);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载报告详情失败');
    } finally {
      setLoading(false);
    }
  };

  const loadCase = async (report: string, caseId: string) => {
    setLoading(true);
    setError('');
    try {
      const data = await appApi.evaluations.getCaseDetail(report, caseId);
      setCaseData(data.case || data);
      setSelectedCaseId(caseId);
      setTab('case');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载用例详情失败');
    } finally {
      setLoading(false);
    }
  };

  const tabs: Array<{ key: Tab; label: string }> = [
    { key: 'realtime', label: '实时监控' },
    { key: 'overview', label: '总览' },
    { key: 'history', label: '运行历史' },
    { key: 'compare', label: '运行对比' },
    { key: 'failures', label: '失败分析' }
  ];

  // Load realtime data
  const loadRealtimeData = async () => {
    try {
      const [recsRes, sumRes] = await Promise.all([
        appApi.evaluations.listRealtimeEvals({ limit: 50 }),
        appApi.evaluations.getRealtimeEvalSummary(realtimeHours),
      ]);
      setRealtimeRecords(recsRes.records || []);
      setRealtimeSummary(sumRes);
    } catch {}
  };

  useEffect(() => {
    if (tab === 'realtime') {
      loadRealtimeData();
      const timer = setInterval(loadRealtimeData, 30000);
      return () => clearInterval(timer);
    }
  }, [tab, realtimeHours]);

  return (
    <>
      <Header title="评测工作台" subtitle="Agent Eval Dashboard" onBack={onBack} />
      <div className="flex border-b border-gray-200 px-2">
        {tabs.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex-1 py-2.5 text-xs font-bold transition-colors ${
              tab === key
                ? 'text-blue-600 border-b-2 border-blue-600'
                : 'text-gray-400'
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <ScreenScroll>
        {loading && (
          <div className="flex items-center justify-center py-12">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-800" />
          </div>
        )}
        {error && (
          <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
        )}
        {!loading && !error && tab === 'realtime' && (
          <RealtimeTab records={realtimeRecords} summary={realtimeSummary} hours={realtimeHours} onHoursChange={setRealtimeHours} onRefresh={loadRealtimeData} />
        )}
        {!loading && !error && tab === 'overview' && (
          <OverviewTab report={reportData} onLoadReport={loadReport} reports={reports} />
        )}
        {!loading && !error && tab === 'history' && (
          <HistoryTab reports={reports} onLoadReport={loadReport} />
        )}
        {!loading && !error && tab === 'compare' && (
          <CompareTab reports={reports} />
        )}
        {!loading && !error && tab === 'case' && (
          <CaseDetailTab caseData={caseData} />
        )}
        {!loading && !error && tab === 'failures' && (
          <FailuresTab report={reportData} onLoadCase={(caseId) => loadCase(selectedReport, caseId)} />
        )}
      </ScreenScroll>
    </>
  );
}

function OverviewTab({ report, onLoadReport, reports }: { report: any; onLoadReport: (name: string) => void; reports: any[] }) {
  return (
    <div className="space-y-4">
      {reports.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-bold text-gray-500">选择报告</h3>
          <select
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm"
            onChange={(e) => onLoadReport(e.target.value)}
          >
            {reports.map((r) => (
              <option key={r.name} value={r.name}>{r.name}</option>
            ))}
          </select>
        </div>
      )}
      {report ? (
        <div className="space-y-3">
          <StatCard label="总用例数" value={report.total_cases ?? report.results?.length ?? '-'} />
          <StatCard label="总体成功率" value={typeof report.overall_success_rate === 'number' ? `${(report.overall_success_rate * 100).toFixed(1)}%` : '-'} />
          <StatCard label="总试验数" value={report.total_trials ?? '-'} />
          <StatCard label="耗时" value={report.duration_seconds ? `${report.duration_seconds.toFixed(1)}s` : '-'} />
          {report.category_breakdown && (
            <div>
              <h3 className="mb-2 text-xs font-bold text-gray-500">分类成功率</h3>
              <div className="space-y-1">
                {Object.entries(report.category_breakdown).map(([cat, data]: [string, any]) => (
                  <div key={cat} className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2 text-sm">
                    <span className="font-medium">{cat}</span>
                    <span className={`font-bold ${data.success_rate >= 0.8 ? 'text-green-600' : data.success_rate >= 0.5 ? 'text-yellow-600' : 'text-red-600'}`}>
                      {(data.success_rate * 100).toFixed(1)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <p className="py-8 text-center text-sm text-gray-400">选择一个报告查看总览</p>
      )}
    </div>
  );
}

function HistoryTab({ reports, onLoadReport }: { reports: any[]; onLoadReport: (name: string) => void }) {
  if (!reports.length) return <p className="py-8 text-center text-sm text-gray-400">暂无评测报告</p>;
  return (
    <div className="space-y-2">
      {reports.map((r) => (
        <button
          key={r.name}
          onClick={() => onLoadReport(r.name)}
          className="w-full rounded-lg border border-gray-100 bg-white p-3 text-left shadow-sm"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-bold">{r.name}</span>
            <span className={`text-xs font-bold ${r.overall_success_rate >= 0.8 ? 'text-green-600' : r.overall_success_rate >= 0.5 ? 'text-yellow-600' : 'text-red-600'}`}>
              {(r.overall_success_rate * 100).toFixed(1)}%
            </span>
          </div>
          <div className="mt-1 flex gap-3 text-[10px] text-gray-400">
            <span>{r.total_cases} 用例</span>
            <span>{r.failed_cases} 失败</span>
            {r.suite && <span>{r.suite}</span>}
            {r.runner && <span>{r.runner}</span>}
          </div>
        </button>
      ))}
    </div>
  );
}

function CompareTab({ reports }: { reports: any[] }) {
  const [baseline, setBaseline] = useState('');
  const [candidate, setCandidate] = useState('');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const doCompare = async () => {
    if (!baseline || !candidate) return;
    setLoading(true);
    setError('');
    try {
      const data = await appApi.evaluations.compareReports(baseline, candidate);
      setResult(data);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '对比失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1 block text-xs font-bold text-gray-500">Baseline</label>
        <select className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm" value={baseline} onChange={(e) => setBaseline(e.target.value)}>
          <option value="">选择基准报告</option>
          {reports.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-xs font-bold text-gray-500">Candidate</label>
        <select className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm" value={candidate} onChange={(e) => setCandidate(e.target.value)}>
          <option value="">选择候选报告</option>
          {reports.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
        </select>
      </div>
      <button onClick={doCompare} disabled={!baseline || !candidate || loading} className="w-full rounded-full bg-black py-3 text-sm font-black text-white disabled:opacity-40">
        {loading ? '对比中...' : '对比运行'}
      </button>
      {error && <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>}
      {result && (
        <div className="space-y-2">
          <h3 className="text-xs font-bold text-gray-500">对比结果</h3>
          <StatCard label="成功率变化" value={typeof result.summary_delta?.overall_success_rate === 'number' ? `${(result.summary_delta.overall_success_rate * 100).toFixed(1)}%` : '-'} />
          <StatCard label="失败用例变化" value={result.summary_delta?.failed_cases ?? '-'} />
          {result.case_changes?.regressions?.length > 0 && (
            <div>
              <h4 className="text-xs font-bold text-red-500">回归 ({result.case_changes.regressions.length})</h4>
              {result.case_changes.regressions.map((r: any) => (
                <div key={r.case_id} className="mt-1 rounded bg-red-50 px-3 py-2 text-xs">{r.case_id}</div>
              ))}
            </div>
          )}
          {result.case_changes?.fixes?.length > 0 && (
            <div>
              <h4 className="text-xs font-bold text-green-500">修复 ({result.case_changes.fixes.length})</h4>
              {result.case_changes.fixes.map((r: any) => (
                <div key={r.case_id} className="mt-1 rounded bg-green-50 px-3 py-2 text-xs">{r.case_id}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CaseDetailTab({ caseData }: { caseData: any }) {
  if (!caseData) return <p className="py-8 text-center text-sm text-gray-400">选择一个用例查看详情</p>;
  return (
    <div className="space-y-3">
      <StatCard label="用例 ID" value={caseData.case_id} />
      <StatCard label="场景" value={caseData.scene || '-'} />
      <StatCard label="类别" value={caseData.category || '-'} />
      <StatCard label="优先级" value={caseData.priority || '-'} />
      <StatCard label="成功率" value={typeof caseData.success_rate === 'number' ? `${(caseData.success_rate * 100).toFixed(1)}%` : '-'} />
      {caseData.avg_scores && (
        <div>
          <h3 className="mb-2 text-xs font-bold text-gray-500">评分</h3>
          <div className="space-y-1">
            {Object.entries(caseData.avg_scores).map(([k, v]: [string, any]) => (
              <div key={k} className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2 text-sm">
                <span className="font-medium">{k}</span>
                <span className="font-bold">{typeof v === 'number' ? v.toFixed(3) : v}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {caseData.trials?.map((trial: any, idx: number) => (
        <div key={idx} className="rounded-lg border border-gray-100 p-3">
          <h4 className="text-xs font-bold text-gray-500">Trial #{trial.trial_number ?? idx}</h4>
          {trial.trace_timeline?.map((event: any, ei: number) => (
            <div key={ei} className="mt-1 flex items-start gap-2 text-xs">
              <span className="mt-0.5 h-2 w-2 flex-shrink-0 rounded-full bg-blue-400" />
              <span className="text-gray-600">{event.event_type}: {event.label || event.tool_name || ''}</span>
            </div>
          ))}
          {trial.failure_class && trial.failure_class !== 'none' && (
            <div className="mt-2 rounded bg-red-50 px-2 py-1 text-xs text-red-600">Failure: {trial.failure_class}</div>
          )}
        </div>
      ))}
    </div>
  );
}

function FailuresTab({ report, onLoadCase }: { report: any; onLoadCase: (caseId: string) => void }) {
  const failedCases = report?.results?.filter((c: any) => c.success_rate < 1) || [];
  if (!report) return <p className="py-8 text-center text-sm text-gray-400">先选择一个报告</p>;
  if (!failedCases.length) return <p className="py-8 text-center text-sm text-gray-400">没有失败的用例 🎉</p>;
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-bold text-red-500">{failedCases.length} 个失败用例</h3>
      {failedCases.map((c: any) => (
        <button
          key={c.case_id}
          onClick={() => onLoadCase(c.case_id)}
          className="w-full rounded-lg border border-red-100 bg-red-50/50 p-3 text-left"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-bold">{c.case_id}</span>
            <span className="text-xs font-bold text-red-600">{(c.success_rate * 100).toFixed(1)}%</span>
          </div>
          <div className="mt-1 flex gap-2 text-[10px] text-gray-400">
            <span>{c.scene}</span>
            <span>{c.category}</span>
            <span>{c.priority}</span>
          </div>
        </button>
      ))}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2.5">
      <span className="text-sm text-gray-500">{label}</span>
      <span className="text-sm font-bold">{value}</span>
    </div>
  );
}

function RealtimeTab({ records, summary, hours, onHoursChange, onRefresh }: {
  records: any[];
  summary: any;
  hours: number;
  onHoursChange: (h: number) => void;
  onRefresh: () => void;
}) {
  const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;
  const fmtMs = (v: number) => `${Math.round(v)}ms`;
  const scoreColor = (v: number) => v >= 0.8 ? 'text-emerald-600' : v >= 0.5 ? 'text-amber-600' : 'text-red-600';

  return (
    <div className="space-y-4 px-4 pb-8">
      {/* Time range */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-bold text-gray-500">时间</span>
        {[1, 6, 24, 72, 168].map(h => (
          <button key={h} onClick={() => onHoursChange(h)}
            className={`px-3 py-1 rounded-full text-xs font-bold ${hours === h ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-500'}`}>
            {h < 24 ? `${h}h` : `${h / 24}d`}
          </button>
        ))}
        <button onClick={onRefresh} className="ml-auto text-xs text-gray-400">↻</button>
      </div>

      {/* Summary */}
      {summary && (
        <div className="grid grid-cols-2 gap-2">
          <StatCard label="对话总数" value={summary.total_evals} />
          <StatCard label="平均质量" value={fmtPct(summary.avg_quality)} />
          <StatCard label="Fallback率" value={fmtPct(summary.fallback_rate)} />
          <StatCard label="无内容率" value={fmtPct(summary.no_content_rate)} />
          <StatCard label="泄露风险" value={fmtPct(summary.leak_rate)} />
          <StatCard label="平均效率" value={fmtPct(summary.avg_efficiency)} />
          <StatCard label="Schema合规" value={fmtPct(summary.avg_schema_compliance)} />
          <StatCard label="平均耗时" value={fmtMs(summary.avg_duration_ms)} />
        </div>
      )}

      {/* Quality trend (simple bar chart) */}
      {summary?.quality_trend?.length > 0 && (
        <div className="rounded-lg bg-white p-3 shadow-sm">
          <h3 className="mb-2 text-xs font-bold text-gray-500">质量趋势</h3>
          <div className="flex h-[60px] items-end gap-[1px]">
            {summary.quality_trend.map((pt: any, i: number) => (
              <div key={i} className="flex-1 rounded-t"
                style={{ height: `${Math.max(4, (pt.avg_quality || 0) * 100)}%`, background: pt.avg_quality >= 0.8 ? '#10b981' : pt.avg_quality >= 0.5 ? '#f59e0b' : '#ef4444' }}
                title={`${pt.hour?.slice(11, 16)}: ${(pt.avg_quality * 100).toFixed(1)}%`} />
            ))}
          </div>
        </div>
      )}

      {/* Recent records */}
      <div className="rounded-lg bg-white shadow-sm overflow-hidden">
        <h3 className="px-3 pt-3 pb-2 text-xs font-bold text-gray-500">最近对话评分</h3>
        <div className="divide-y divide-gray-50">
          {records.slice(0, 30).map((r: any) => (
            <div key={r.id} className="flex items-center gap-2 px-3 py-2.5 text-xs">
              <span className="font-mono text-gray-400 w-[72px]">{r.timestamp?.slice(5, 16) || '-'}</span>
              <span className="text-gray-600 w-[48px]">{r.scene || '-'}</span>
              <span className={`font-mono font-bold w-[48px] text-right ${scoreColor(r.overall_quality)}`}>{fmtPct(r.overall_quality)}</span>
              {r.is_fallback && <span className="px-1.5 py-0.5 rounded bg-red-50 text-red-600 text-[10px] font-bold">FB</span>}
              <span className="text-gray-400 flex-1 text-right">{fmtMs(r.total_duration_ms)}</span>
            </div>
          ))}
          {records.length === 0 && <div className="px-4 py-8 text-center text-gray-400 text-sm">暂无数据</div>}
        </div>
      </div>
    </div>
  );
}
