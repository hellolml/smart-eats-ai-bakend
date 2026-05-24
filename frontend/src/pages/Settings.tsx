import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Bot, ChevronRight, HelpCircle, Info, Puzzle, Settings2, Shield } from 'lucide-react';
import { motion } from 'framer-motion';

const settingsItems = [
    {
        title: '安全设置',
        desc: '修改密码与账号保护',
        icon: Shield,
        color: 'text-purple-500',
        bgColor: 'bg-purple-50',
        path: '/security-settings'
    },
    {
        title: 'AI 模型设置',
        desc: '配置 Base URL、API Key 与默认模型',
        icon: Bot,
        color: 'text-indigo-500',
        bgColor: 'bg-indigo-50',
        path: '/model-settings'
    },
    {
        title: 'Skill 管理',
        desc: '管理内置能力与外部 Skill',
        icon: Puzzle,
        color: 'text-emerald-600',
        bgColor: 'bg-emerald-50',
        path: '/settings/skills'
    },
    {
        title: '帮助中心',
        desc: '常见问题与指南',
        icon: HelpCircle,
        color: 'text-blue-500',
        bgColor: 'bg-blue-50',
        path: '#'
    },
    {
        title: '关于',
        desc: '版本 v0.0.1',
        icon: Info,
        color: 'text-gray-500',
        bgColor: 'bg-gray-50',
        path: '#'
    }
];

export default function Settings() {
    const navigate = useNavigate();

    return (
        <div className="h-full flex flex-col overflow-hidden pb-20 md:pb-4 animate-in fade-in duration-500 relative">
            <div className="absolute top-0 left-0 right-0 h-24 bg-gradient-to-b from-[#7E57FF]/10 to-transparent -z-10" />

            <motion.section
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex-shrink-0 pt-4 pb-6 px-6 bg-white shadow-sm"
            >
                <div className="flex items-center gap-3">
                    <div className="flex h-12 w-12 items-center justify-center rounded-[1.25rem] bg-[#7E57FF]/10 text-[#7E57FF]">
                        <Settings2 size={22} />
                    </div>
                    <div>
                        <h1 className="text-xl font-black text-gray-800">设置</h1>
                        <p className="text-xs text-gray-400 mt-0.5">管理安全、模型与 Agent 能力</p>
                    </div>
                </div>
            </motion.section>

            <div className="flex-1 overflow-hidden mt-2">
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="bg-white border-y border-purple-50 shadow-sm overflow-hidden divide-y divide-gray-50"
                >
                    {settingsItems.map((item) => (
                        <button
                            key={item.title}
                            onClick={() => item.path !== '#' && navigate(item.path)}
                            className="w-full px-0 py-2.5 flex items-center justify-between hover:bg-gray-50 transition-all group active:bg-purple-50/30"
                        >
                            <div className="flex items-center gap-3 px-6">
                                <div className={`${item.bgColor} ${item.color} p-2 rounded-lg group-hover:scale-110 transition-transform`}>
                                    <item.icon size={16} />
                                </div>
                                <div className="text-left">
                                    <span className="text-xs font-bold text-gray-800 block">{item.title}</span>
                                    <span className="text-[9px] text-gray-400 font-medium">{item.desc}</span>
                                </div>
                            </div>
                            <div className="px-6">
                                <ChevronRight
                                    size={14}
                                    className="text-gray-300 group-hover:text-[#7E57FF] group-hover:translate-x-1 transition-all"
                                />
                            </div>
                        </button>
                    ))}
                </motion.div>
            </div>
        </div>
    );
}
