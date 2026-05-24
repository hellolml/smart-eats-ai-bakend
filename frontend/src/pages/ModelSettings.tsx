import React from 'react';
import { Bot, ChevronLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import ModelConfigPanel from '@/components/ModelConfigPanel';

export default function ModelSettings() {
    const navigate = useNavigate();

    return (
        <div className="min-h-full pb-20 md:pb-6">
            <div className="mb-4 flex items-center gap-3 rounded-[1.5rem] border border-purple-50 bg-white p-4 shadow-sm">
                <button
                    type="button"
                    onClick={() => navigate(-1)}
                    className="rounded-xl p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
                    title="返回"
                >
                    <ChevronLeft size={20} />
                </button>
                <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#7E57FF]/10 text-[#7E57FF]">
                    <Bot size={20} />
                </div>
                <div>
                    <h1 className="text-lg font-black text-gray-800">AI 模型设置</h1>
                    <p className="text-xs text-gray-400">全局管理 OpenAI-compatible / Anthropic 模型配置</p>
                </div>
            </div>
            <ModelConfigPanel />
        </div>
    );
}
