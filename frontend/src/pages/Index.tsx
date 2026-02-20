import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    Sun,
    Cloud,
    CloudRain,
    CloudSnow,
    CloudLightning,
    Heart,
    Brain,
    Sparkles,
    RotateCw,
    PackageSearch,
    MessageSquare,
    LogIn
} from 'lucide-react';
import { motion } from 'framer-motion';
import toast from 'react-hot-toast';
import { AppHomeOverview, AppProfile, appApi, authStore } from "@/services/app-api";

function normalizeWeatherStatus(raw: string): string {
    const status = raw.trim();
    if (!status) return '';
    const lower = status.toLowerCase();

    if (status.includes('雷') || lower.includes('thunder') || lower.includes('storm')) return '雷雨';
    if (status.includes('雪') || lower.includes('snow') || lower.includes('sleet') || lower.includes('hail')) return '雪';
    if (status.includes('雨') || lower.includes('rain') || lower.includes('drizzle') || lower.includes('shower')) return '雨';
    if (status.includes('多云') || lower.includes('cloud')) return '多云';
    if (status.includes('阴') || lower.includes('overcast')) return '阴';
    if (status.includes('晴') || lower.includes('sunny') || lower.includes('clear')) return '晴';
    return status;
}

const Index = () => {
    const navigate = useNavigate();
    const [profile, setProfile] = useState<AppProfile | null>(null);
    const [overview, setOverview] = useState<AppHomeOverview | null>(null);
    const [savingGoalState, setSavingGoalState] = useState(false);
    const [customEditorType, setCustomEditorType] = useState<'goal' | 'state' | null>(null);
    const [customInput, setCustomInput] = useState('');
    const isLoggedIn = authStore.isLoggedIn();

    useEffect(() => {
        const fetchData = async () => {
            if (!isLoggedIn) return;
            try {
                const p = await appApi.me.get();
                setProfile(p);

                const fallbackOverview = await appApi.me.getHomeOverview();
                setOverview(fallbackOverview);

                if (typeof window === 'undefined' || !window.navigator.geolocation) {
                    return;
                }

                window.navigator.geolocation.getCurrentPosition(
                    (position) => {
                        void (async () => {
                            try {
                                const localizedOverview = await appApi.me.getHomeOverview({
                                    lat: position.coords.latitude,
                                    lng: position.coords.longitude
                                });
                                setOverview(localizedOverview);
                            } catch (error) {
                                console.warn('home overview geolocation fallback:', error);
                            }
                        })();
                    },
                    (geoError) => {
                        if (geoError.code === 1) {
                            toast('未授权定位，已使用默认北京天气', { duration: 2200 });
                        }
                    },
                    {
                        timeout: 5000,
                        maximumAge: 5 * 60 * 1000
                    }
                );
            } catch (e) {
                console.error('fetch error:', e);
                toast.error('主页数据加载失败');
            }
        };
        void fetchData();
    }, [isLoggedIn]);

    const goalOptions = ['减脂', '增肌', '维持体重', '控糖', '均衡饮食'];
    const stateOptions = ['精神满满', '有点累', '压力大', '想吃清淡', '很饿'];
    const displayName = overview?.name || profile?.name || '美食家';
    const displayGoal = overview?.health_goal || profile?.health_goal || '';
    const displayState = overview?.current_state || profile?.current_state || '';
    const weatherCity = overview?.weather?.city?.trim() || '';
    const weatherTemp = overview?.weather?.temperature_text?.trim() || '--°';
    const weatherStatusRaw = overview?.weather?.status?.trim() || '';
    const weatherStatus = normalizeWeatherStatus(weatherStatusRaw);

    const WeatherIcon = (() => {
        if (weatherStatus.includes('雷')) return CloudLightning;
        if (weatherStatus.includes('雪')) return CloudSnow;
        if (weatherStatus.includes('雨')) return CloudRain;
        if (weatherStatus.includes('多云') || weatherStatus.includes('阴')) return Cloud;
        return Sun;
    })();

    const weatherDisplay = [weatherCity, weatherTemp, weatherStatus].filter(Boolean).join(' · ') || '天气待更新';
    const goalItems = displayGoal && !goalOptions.includes(displayGoal)
        ? [displayGoal, ...goalOptions]
        : goalOptions;
    const stateItems = displayState && !stateOptions.includes(displayState)
        ? [displayState, ...stateOptions]
        : stateOptions;

    const handleUpdateGoalState = async (patch: { health_goal?: string; current_state?: string }) => {
        if (!isLoggedIn) return false;
        try {
            setSavingGoalState(true);
            const updated = await appApi.me.updateGoalState(patch);
            setProfile((prev) => prev ? {
                ...prev,
                health_goal: updated.health_goal,
                current_state: updated.current_state
            } : prev);
            setOverview((prev) => ({
                ...(prev || {}),
                health_goal: updated.health_goal,
                current_state: updated.current_state
            }));
            toast.success('目标状态已更新');
            return true;
        } catch (e) {
            console.error('update goal/state error:', e);
            toast.error('更新目标状态失败');
            return false;
        } finally {
            setSavingGoalState(false);
        }
    };

    const handleOpenCustomEditor = (type: 'goal' | 'state') => {
        setCustomEditorType(type);
        setCustomInput(type === 'goal' ? displayGoal : displayState);
    };

    const handleCloseCustomEditor = () => {
        if (savingGoalState) return;
        setCustomEditorType(null);
        setCustomInput('');
    };

    const handleSubmitCustomEditor = async () => {
        if (!customEditorType) return;
        const value = customInput.trim();
        const currentValue = customEditorType === 'goal' ? displayGoal : displayState;
        if (!value || value === currentValue) {
            handleCloseCustomEditor();
            return;
        }
        const ok = await handleUpdateGoalState(
            customEditorType === 'goal'
                ? { health_goal: value }
                : { current_state: value }
        );
        if (ok) {
            setCustomEditorType(null);
            setCustomInput('');
        }
    };

    const customEditorTitle = customEditorType === 'goal' ? '自定义目标' : '自定义状态';
    const customEditorPlaceholder = customEditorType === 'goal' ? '例如：低碳饮食' : '例如：今晚想吃热乎的';

    if (!isLoggedIn) {
        return (
            <div className="min-h-full flex flex-col gap-3 md:gap-4 py-2">
                <div className="bg-white rounded-[2rem] p-5 md:p-6 shadow-sm border border-purple-50 flex-shrink-0">
                    <h2 className="text-lg md:text-2xl font-black text-gray-800">欢迎来到 吃点啥？</h2>
                    <p className="text-xs md:text-sm text-gray-500 mt-2">
                        登录后可使用更多功能：AI 对话、家里做、出去吃、个人中心等。
                    </p>
                    <button
                        onClick={() => navigate('/login')}
                        className="mt-4 px-5 py-2.5 bg-[#7E57FF] text-white rounded-full font-bold text-xs md:text-sm flex items-center gap-2 active:scale-95 transition-transform"
                    >
                        <LogIn size={16} />
                        立即登录
                    </button>
                </div>

                <div className="flex-1 min-h-0 grid grid-cols-1 gap-3 md:gap-4">
                    <motion.div
                        whileTap={{ scale: 0.97 }}
                        onClick={() => navigate('/blind-box')}
                        className="bg-[#FFDD77] p-5 md:p-6 rounded-[2rem] md:rounded-[2.5rem] flex flex-col justify-between min-h-[220px] cursor-pointer shadow-sm border border-yellow-200/50 group"
                    >
                        <div className="w-10 h-10 bg-white/60 rounded-xl md:rounded-2xl flex items-center justify-center group-hover:rotate-12 transition-transform">
                            <PackageSearch size={22} className="text-gray-700" />
                        </div>
                        <div>
                            <h3 className="font-bold text-gray-800 text-base md:text-lg">盲盒摇一摇</h3>
                            <p className="text-xs text-gray-600 mt-1 opacity-80">随机抽取今日美味惊喜</p>
                        </div>
                    </motion.div>

                    <motion.div
                        whileTap={{ scale: 0.97 }}
                        onClick={() => navigate('/wheel')}
                        className="bg-[#E6DDFF] p-5 md:p-6 rounded-[2rem] md:rounded-[2.5rem] flex flex-col justify-between min-h-[220px] cursor-pointer shadow-sm border border-purple-200/50 group"
                    >
                        <div className="w-10 h-10 bg-white/60 rounded-xl md:rounded-2xl flex items-center justify-center group-hover:rotate-12 transition-transform">
                            <RotateCw size={22} className="text-[#7E57FF]" />
                        </div>
                        <div>
                            <h3 className="font-bold text-gray-800 text-base md:text-lg">幸运大转盘</h3>
                            <p className="text-xs text-gray-600 mt-1 opacity-80">自定义选项，纠结症克星</p>
                        </div>
                    </motion.div>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-full flex flex-col gap-3 md:gap-4 py-2">
            <div className="bg-white rounded-[2rem] p-4 md:p-5 shadow-sm border border-purple-50 flex-shrink-0">
                <div className="flex justify-between items-start mb-4 md:mb-4">
                    <div>
                        <h2 className="text-base md:text-xl font-bold text-gray-800">
                            你好，{displayName}
                        </h2>
                        <p className="text-gray-400 text-[10px] md:text-xs mt-0.5">
                            今天想吃点什么特别的？
                        </p>
                    </div>
                    <div className="bg-[#FFCC33] px-2.5 py-1 rounded-full text-[9px] font-bold flex items-center gap-1 shadow-sm">
                        <WeatherIcon size={10} className="md:w-3 md:h-3" />{weatherDisplay}
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-2 md:gap-3">
                    <div className="bg-purple-50 p-2.5 md:p-3 rounded-xl md:rounded-2xl flex items-center gap-2 md:gap-3 border border-purple-100/50">
                        <div className="w-7 h-7 md:w-8 md:h-8 bg-white rounded-lg flex items-center justify-center text-[#7E57FF] shadow-sm">
                            <Heart size={12} className="md:w-3.5 md:h-3.5" />
                        </div>
                        <div>
                            <p className="text-[7px] md:text-[8px] text-purple-400 uppercase font-bold">
                                目标
                            </p>
                            <div className="mt-1 flex flex-wrap gap-1.5">
                                {goalItems.map((item) => {
                                    const active = displayGoal === item;
                                    return (
                                        <button
                                            key={item}
                                            type="button"
                                            disabled={savingGoalState}
                                            onClick={() => {
                                                if (active) return;
                                                void handleUpdateGoalState({ health_goal: item });
                                            }}
                                            className={`px-2 py-0.5 rounded-full text-[9px] md:text-[10px] font-bold transition-colors ${
                                                active
                                                    ? 'bg-[#7E57FF] text-white'
                                                    : 'bg-white text-gray-600 border border-purple-200 hover:bg-purple-100'
                                            } ${savingGoalState ? 'opacity-60 cursor-not-allowed' : ''}`}
                                        >
                                            {item}
                                        </button>
                                    );
                                })}
                                <button
                                    type="button"
                                    disabled={savingGoalState}
                                    onClick={() => handleOpenCustomEditor('goal')}
                                    className={`px-2 py-0.5 rounded-full text-[9px] md:text-[10px] font-bold transition-colors bg-white text-[#7E57FF] border border-dashed border-purple-300 hover:bg-purple-100 ${savingGoalState ? 'opacity-60 cursor-not-allowed' : ''}`}
                                >
                                    +自定义
                                </button>
                            </div>
                        </div>
                    </div>
                    <div className="bg-yellow-50 p-2.5 md:p-3 rounded-xl md:rounded-2xl flex items-center gap-2 md:gap-3 border border-yellow-100/50">
                        <div className="w-7 h-7 md:w-8 md:h-8 bg-white rounded-lg flex items-center justify-center text-[#FFCC33] shadow-sm">
                            <Brain size={14} className="md:w-3.5 md:h-3.5" />
                        </div>
                        <div>
                            <p className="text-[7px] md:text-[8px] text-yellow-600 uppercase font-bold">
                                状态
                            </p>
                            <div className="mt-1 flex flex-wrap gap-1.5">
                                {stateItems.map((item) => {
                                    const active = displayState === item;
                                    return (
                                        <button
                                            key={item}
                                            type="button"
                                            disabled={savingGoalState}
                                            onClick={() => {
                                                if (active) return;
                                                void handleUpdateGoalState({ current_state: item });
                                            }}
                                            className={`px-2 py-0.5 rounded-full text-[9px] md:text-[10px] font-bold transition-colors ${
                                                active
                                                    ? 'bg-[#FFCC33] text-gray-800'
                                                    : 'bg-white text-gray-600 border border-yellow-200 hover:bg-yellow-100'
                                            } ${savingGoalState ? 'opacity-60 cursor-not-allowed' : ''}`}
                                        >
                                            {item}
                                        </button>
                                    );
                                })}
                                <button
                                    type="button"
                                    disabled={savingGoalState}
                                    onClick={() => handleOpenCustomEditor('state')}
                                    className={`px-2 py-0.5 rounded-full text-[9px] md:text-[10px] font-bold transition-colors bg-white text-yellow-700 border border-dashed border-yellow-300 hover:bg-yellow-100 ${savingGoalState ? 'opacity-60 cursor-not-allowed' : ''}`}
                                >
                                    +自定义
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div className="flex-1 min-h-0 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 md:gap-4">
                <div className="h-full min-h-0 lg:col-span-2">
                    <motion.div
                        whileHover={{ scale: 1.005 }}
                        className="w-full h-full bg-[#7E57FF] text-white rounded-[2rem] md:rounded-[2.5rem] shadow-xl shadow-purple-100 flex-flex-col items-center justify-center gap-2 relative overflow-hidden group p-4 md:p-6"
                    >
                        <div className="w-12 h-12 md:w-20 md:h-20 bg-white/20 rounded-full flex items-center justify-center backdrop-blur-sm mb-1 md:mb-2">
                            <Sparkles size={32} className="md:w-8 md:h-8 text-white animate-pulse" />
                        </div>
                        <div className="text-center z-10 flex flex-col items-center">
                            <span className="block text-lg md:text-2xl font-black tracking-tight">
                                帮我决定吃点啥？
                            </span>
                            <span className="block text-[9px] md:text-[10px] font-normal opacity-70 mt-1 md:mt-2 mb-4 md:mb-6 max-w-[160px] md:max-w-[200px] mx-auto">
                                基于冰箱食材与健康目标智能推荐
                            </span>

                            <button
                                onClick={() => navigate('/ai-chat')}
                                className="px-6 md:px-8 py-2.5 md:py-3 bg-white text-[#7E57FF] rounded-full font-bold text-[11px] md:text-sm shadow-lg flex items-center justify-center gap-2 hover:scale-105 active:scale-95 transition-all"
                            >
                                <MessageSquare size={16}
                                    fill="currentColor"
                                    className="md:w-4 md:h-4 opacity-20"
                                />
                                开始对话
                            </button>
                        </div>
                        <div className="absolute -bottom-10 -right-10 w-32 h-32 bg-white/10 rounded-full blur-2xl" />
                        <div className="absolute -top-10 -left-10 w-32 h-32 bg-purple-400/20 rounded-full blur-2xl" />
                    </motion.div>
                </div>

                <div className="grid grid-cols-1 gap-3 md:gap-4 h-full min-h-0">
                    <motion.div
                        whileTap={{ scale: 0.96 }}
                        onClick={() => navigate('/blind-box')}
                        className="bg-[#FFDD77] p-4 md:p-5 rounded-[2rem] md:rounded-[2.5rem] flex flex-col justify-between h-full min-h-0 cursor-pointer shadow-sm border border-yellow-200/50 group">
                        <div className="w-8 h-8 md:w-10 md:h-10 bg-white/60 rounded-xl md:rounded-2xl flex items-center justify-center group-hover:rotate-12 transition-transform">
                            <PackageSearch size={22} className="md:w-5 md:h-5 text-gray-700" />
                        </div>
                        <div>
                            <h3 className="font-bold text-gray-800 text-xs md:text-base">
                                盲盒摇一摇
                            </h3>
                            <p className="text-[8px] md:text-[10px] text-gray-600 mt-0.5 md:mt-1 opacity-80 line-clamp-1">
                                随机抽取今日美味惊喜
                            </p>
                        </div>
                    </motion.div>

                    <motion.div
                        whileTap={{ scale: 0.96 }}
                        onClick={() => navigate('/wheel')}
                        className="bg-[#E6DDFF] p-4 md:p-5 rounded-[2rem] rounded-[2.5rem] flex flex-col justify-between h-full min-h-0 cursor-pointer shadow-sm border border-purple-200/50 group">
                        <div className="w-8 h-8 md:w-10 md:h-10 bg-white/60 rounded-xl md:rounded-2xl flex items-center justify-center group-hover:rotate-12 transition-transform">
                            <RotateCw size={22} className="md:w-5 md:h-5 text-[#7E57FF]" />
                        </div>
                        <div>
                            <h3 className="font-bold text-gray-800 text-sm md:text-base">
                                幸运大转盘
                            </h3>
                            <p className="text-[8px] md:text-[10px] text-gray-600 mt-0.5 md:mt-1 opacity-80 line-clamp-1">
                                自定义选项，纠结症克星
                            </p>
                        </div>
                    </motion.div>
                </div>
            </div>

            {customEditorType && (
                <div
                    className="fixed inset-0 z-50 bg-black/25 backdrop-blur-[1px] flex items-center justify-center px-5"
                    onClick={handleCloseCustomEditor}
                >
                    <div
                        className="w-full max-w-sm bg-white rounded-[1.75rem] p-4 md:p-5 shadow-2xl border border-purple-100"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <h3 className="text-sm md:text-base font-bold text-gray-800">{customEditorTitle}</h3>
                        <p className="text-[10px] md:text-xs text-gray-500 mt-1">可输入你自己的偏好描述</p>

                        <input
                            autoFocus
                            type="text"
                            value={customInput}
                            onChange={(e) => setCustomInput(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key !== 'Enter') return;
                                e.preventDefault();
                                void handleSubmitCustomEditor();
                            }}
                            placeholder={customEditorPlaceholder}
                            disabled={savingGoalState}
                            className="mt-3 w-full rounded-2xl border border-purple-200 bg-purple-50/50 px-3 py-2 text-xs md:text-sm text-gray-800 outline-none focus:ring-2 focus:ring-purple-200"
                        />

                        <div className="mt-4 flex justify-end gap-2">
                            <button
                                type="button"
                                disabled={savingGoalState}
                                onClick={handleCloseCustomEditor}
                                className="px-3 py-1.5 rounded-full text-[11px] md:text-xs font-bold text-gray-500 bg-gray-100 hover:bg-gray-200 transition-colors disabled:opacity-60"
                            >
                                取消
                            </button>
                            <button
                                type="button"
                                disabled={savingGoalState}
                                onClick={() => {
                                    void handleSubmitCustomEditor();
                                }}
                                className="px-3 py-1.5 rounded-full text-[11px] md:text-xs font-bold text-white bg-[#7E57FF] hover:opacity-90 transition-opacity disabled:opacity-60"
                            >
                                {savingGoalState ? '保存中...' : '保存'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default Index;
