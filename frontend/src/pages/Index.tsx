import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    Sun,
    Heart,
    Brain,
    Sparkles,
    RotateCw,
    PackageSearch,
    MessageSquare,
    LogIn
} from 'lucide-react';
import { motion } from 'framer-motion';
import { appApi, authStore } from "@/services/app-api";

const Index = () => {
    const navigate = useNavigate();
    const [profile, setProfile] = useState<any>(null);
    const isLoggedIn = authStore.isLoggedIn();

    useEffect(() => {
        const fetchData = async () => {
            if (!isLoggedIn) return;
            try {
                const p = await appApi.me.get();
                setProfile(p);
            } catch (e) {
                console.error('fetch error:', e);
            }
        };
        fetchData();
    }, [isLoggedIn]);

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
                            你好，{profile?.name || '美食家'}
                        </h2>
                        <p className="text-gray-400 text-[10px] md:text-xs mt-0.5">
                            今天想吃点什么特别的？
                        </p>
                    </div>
                    <div className="bg-[#FFCC33] px-2.5 py-1 rounded-full text-[9px] font-bold flex items-center gap-1 shadow-sm">
                        <Sun size={10} className="md:w-3 md:h-3" />32°晴
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
                            <p className="text-[10px] md:text-xs font-bold text-gray-700">
                                {profile?.health_goal || '减脂'}
                            </p>
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
                            <p className="text-10px md:text-xs font-bold text-gray-700">有点累</p>
                        </div>
                    </div>
                </div>
            </div>
            <div className="flex-1 min-h-0 grid grid-cols-1 gap-3 md:gap-4">
                <div className="h-full min-h-0">
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
        </div>
    );
};

export default Index;
