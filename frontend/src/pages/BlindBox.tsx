import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronLeft, Navigation, RefreshCw, Sparkles } from 'lucide-react';
import toast from 'react-hot-toast';
import { AppDecisionResult, AppQuickFilterState, appApi, authStore } from '@/services/app-api';

const BlindBox: React.FC = () => {
    const navigate = useNavigate();
    const isLoggedIn = authStore.isLoggedIn();
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<AppDecisionResult | null>(null);
    const [flow, setFlow] = useState<AppQuickFilterState | null>(null);

    const openLink = (url?: string) => {
        if (!url) return;
        window.open(url, '_blank', 'noopener,noreferrer');
    };

    const doBlindbox = async () => {
        setLoading(true);
        try {
            const data = await appApi.decisions.blindbox({ scene: 'blind_box' });
            setResult(data);
            setFlow(null);
        } catch (e) {
            console.error(e);
            toast('盲盒决策失败，请稍后重试', { duration: 1800 });
        } finally {
            setLoading(false);
        }
    };

    const startQuickFilter = async () => {
        setLoading(true);
        try {
            const data = await appApi.decisions.quickFilterStart({ query: '晚饭' });
            setFlow(data);
            setResult(null);
        } catch (e) {
            console.error(e);
            toast('启动快速筛选失败', { duration: 1800 });
        } finally {
            setLoading(false);
        }
    };

    const answerQuickFilter = async (answer: string) => {
        if (!flow?.flow_id) return;
        setLoading(true);
        try {
            const next = await appApi.decisions.quickFilterAnswer({
                flow_id: flow.flow_id,
                answer
            });
            setFlow(next);
            if (next.done && next.result) {
                setResult(next.result);
            }
        } catch (e) {
            console.error(e);
            toast('提交答案失败', { duration: 1800 });
        } finally {
            setLoading(false);
        }
    };

    if (!isLoggedIn) {
        return (
            <div className="h-full flex items-center justify-center">
                <div className="bg-white rounded-2xl p-6 border border-purple-50 text-center">
                    <p className="text-sm text-gray-600">先登录再帮你拍板今晚吃啥</p>
                    <button
                        onClick={() => navigate('/login')}
                        className="mt-4 px-5 py-2 bg-[#7E57FF] text-white rounded-full text-sm font-bold"
                    >
                        去登录
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto no-scrollbar bg-[#FFF9F2] pb-20">
            <header className="sticky top-0 h-14 bg-white/90 backdrop-blur border-b border-purple-50 flex items-center px-3 z-20">
                <button onClick={() => navigate('/')} className="p-2 text-gray-600">
                    <ChevronLeft size={22} />
                </button>
                <h1 className="flex-1 text-center mr-8 font-bold text-gray-800">美食盲盒</h1>
            </header>

            <div className="p-4 space-y-4">
                <div className="bg-white rounded-2xl border border-purple-50 p-4">
                    <h2 className="font-bold text-gray-800 flex items-center gap-2"><Sparkles size={16} /> 一键拍板</h2>
                    <p className="text-xs text-gray-500 mt-1">不纠结，直接给你一个今晚最优解</p>
                    <button
                        disabled={loading}
                        onClick={() => void doBlindbox()}
                        className="mt-3 w-full py-3 rounded-xl bg-[#7E57FF] text-white font-bold disabled:opacity-60"
                    >
                        {loading ? '生成中...' : '开盲盒'}
                    </button>
                    <button
                        disabled={loading}
                        onClick={() => void startQuickFilter()}
                        className="mt-2 w-full py-3 rounded-xl bg-white border border-purple-200 text-[#7E57FF] font-bold disabled:opacity-60"
                    >
                        先问我 2-3 个问题再推荐
                    </button>
                </div>

                {flow && !flow.done && flow.next_question && (
                    <div className="bg-white rounded-2xl border border-purple-50 p-4">
                        <h3 className="font-semibold text-gray-800">第 {Math.min(flow.round, 3)} 轮</h3>
                        <p className="text-sm text-gray-700 mt-2">{flow.next_question.question}</p>
                        <div className="grid grid-cols-2 gap-2 mt-3">
                            {flow.next_question.options.map((opt) => (
                                <button
                                    key={opt}
                                    disabled={loading}
                                    onClick={() => void answerQuickFilter(opt)}
                                    className="py-2 rounded-lg border border-purple-200 text-sm font-medium text-[#7E57FF] disabled:opacity-60"
                                >
                                    {opt}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {result && (
                    <div className="bg-white rounded-2xl border border-purple-50 p-4 space-y-3">
                        <div className="text-xs text-purple-500 font-bold uppercase">本次唯一决定</div>
                        <div className="text-xl font-black text-gray-800">{result.decision.title}</div>
                        {typeof result.decision.confidence === 'number' && (
                            <div className="text-xs text-gray-500">置信度：{Math.round(result.decision.confidence * 100)}%</div>
                        )}

                        <div>
                            <div className="text-sm font-semibold text-gray-700">推荐理由</div>
                            <ul className="mt-2 space-y-1 text-sm text-gray-600 list-disc pl-5">
                                {result.reasons.map((r, idx) => (
                                    <li key={`${r}-${idx}`}>{r}</li>
                                ))}
                            </ul>
                        </div>

                        {result.actions?.length > 0 && (
                            <div>
                                <div className="text-sm font-semibold text-gray-700">下一步动作</div>
                                <div className="mt-2 space-y-2">
                                    {result.actions.map((a, idx) => (
                                        <button
                                            key={`${a.label}-${idx}`}
                                            onClick={() => openLink(a.url)}
                                            className="w-full py-2.5 rounded-xl bg-purple-50 text-[#7E57FF] text-sm font-bold flex items-center justify-center gap-2"
                                        >
                                            <Navigation size={14} /> {a.label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        <button
                            onClick={() => {
                                setResult(null);
                                setFlow(null);
                            }}
                            className="w-full py-2.5 rounded-xl border border-gray-200 text-gray-600 text-sm font-medium flex items-center justify-center gap-2"
                        >
                            <RefreshCw size={14} /> 重新来一次
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default BlindBox;
