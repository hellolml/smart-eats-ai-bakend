import React, { useEffect, useState } from 'react';
import { CheckCircle2, Plus, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { AppLlmProviderConfig, AppLlmProviderType, appApi } from '@/services/app-api';

type ModelConfigForm = {
    id?: string;
    display_name: string;
    provider_type: AppLlmProviderType;
    base_url: string;
    api_key: string;
    model_planner: string;
    model_writer: string;
    model_vision_planner: string;
    enabled: boolean;
    is_default: boolean;
};

const emptyModelConfigForm: ModelConfigForm = {
    display_name: '',
    provider_type: 'openai_compatible',
    base_url: '',
    api_key: '',
    model_planner: '',
    model_writer: '',
    model_vision_planner: '',
    enabled: true,
    is_default: false
};

type ModelConfigPanelProps = {
    onChanged?: () => void | Promise<void>;
};

export default function ModelConfigPanel({ onChanged }: ModelConfigPanelProps) {
    const [configs, setConfigs] = useState<AppLlmProviderConfig[]>([]);
    const [form, setForm] = useState<ModelConfigForm>(emptyModelConfigForm);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);

    const refresh = React.useCallback(async () => {
        const rows = await appApi.chat.listModelConfigs();
        setConfigs(rows || []);
        await onChanged?.();
    }, [onChanged]);

    useEffect(() => {
        void refresh()
            .catch((error) => {
                console.error('load model configs failed:', error);
                toast.error('加载模型配置失败');
            })
            .finally(() => setLoading(false));
    }, [refresh]);

    const editConfig = React.useCallback((config: AppLlmProviderConfig) => {
        setForm({
            id: config.id,
            display_name: config.display_name,
            provider_type: config.provider_type || 'openai_compatible',
            base_url: config.base_url,
            api_key: '',
            model_planner: config.model_planner,
            model_writer: config.model_writer || '',
            model_vision_planner: config.model_vision_planner || '',
            enabled: config.enabled,
            is_default: config.is_default
        });
    }, []);

    const saveConfig = React.useCallback(async () => {
        if (!form.display_name.trim() || !form.base_url.trim() || !form.model_planner.trim()) {
            toast.error('请填写名称、Base URL 和模型名称');
            return;
        }
        if (!form.id && !form.api_key.trim()) {
            toast.error('请填写 API Key');
            return;
        }

        setSaving(true);
        try {
            const payload = {
                display_name: form.display_name.trim(),
                provider_type: form.provider_type,
                base_url: form.base_url.trim(),
                model_planner: form.model_planner.trim(),
                model_writer: form.model_writer.trim() || null,
                model_vision_planner: form.model_vision_planner.trim() || null,
                enabled: form.enabled,
                is_default: form.is_default,
                ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {})
            };
            if (form.id) {
                await appApi.chat.updateModelConfig(form.id, payload);
            } else {
                await appApi.chat.createModelConfig(payload as typeof payload & { api_key: string });
            }
            await refresh();
            setForm(emptyModelConfigForm);
            toast.success('模型配置已保存');
        } catch (error) {
            console.error('save model config failed:', error);
            toast.error('保存模型配置失败');
        } finally {
            setSaving(false);
        }
    }, [form, refresh]);

    const testConfig = React.useCallback(async () => {
        setTesting(true);
        try {
            const result = form.id && !form.api_key.trim()
                ? await appApi.chat.testModelConfig({ config_id: form.id })
                : await appApi.chat.testModelConfig({
                    provider_type: form.provider_type,
                    base_url: form.base_url.trim(),
                    api_key: form.api_key.trim(),
                    model: form.model_planner.trim()
                });
            if (result.status === 'success') {
                toast.success('连接测试成功');
            } else {
                toast.error(result.error || '连接测试失败');
            }
            if (form.id) {
                await refresh();
            }
        } catch (error) {
            console.error('test model config failed:', error);
            toast.error('连接测试失败');
        } finally {
            setTesting(false);
        }
    }, [form, refresh]);

    const deleteConfig = React.useCallback(async (configId: string) => {
        if (!window.confirm('确定要删除这个模型配置吗？')) return;
        try {
            await appApi.chat.deleteModelConfig(configId);
            await refresh();
            if (form.id === configId) {
                setForm(emptyModelConfigForm);
            }
            toast.success('已删除模型配置');
        } catch (error) {
            console.error('delete model config failed:', error);
            toast.error('删除模型配置失败');
        }
    }, [form.id, refresh]);

    const setDefault = React.useCallback(async (configId: string) => {
        try {
            await appApi.chat.setDefaultModelConfig(configId);
            await refresh();
            toast.success('已设为默认模型');
        } catch (error) {
            console.error('set default model config failed:', error);
            toast.error('设置默认模型失败');
        }
    }, [refresh]);

    return (
        <div className="grid gap-4 md:grid-cols-[1fr_1.25fr]">
            <section className="rounded-[1.5rem] border border-purple-50 bg-white p-4 shadow-sm">
                <div className="mb-3 flex items-center justify-between">
                    <div>
                        <h2 className="text-sm font-bold text-gray-800">自定义模型</h2>
                        <p className="mt-1 text-xs text-gray-400">用户级全局配置，所有 AI 能力可复用</p>
                    </div>
                    <button
                        type="button"
                        onClick={() => setForm(emptyModelConfigForm)}
                        className="inline-flex items-center gap-1 rounded-xl border border-dashed border-gray-300 px-3 py-2 text-xs text-gray-600 hover:border-purple-200 hover:text-[#7E57FF]"
                    >
                        <Plus size={14} /> 新增
                    </button>
                </div>
                {loading ? (
                    <div className="rounded-xl bg-gray-50 p-4 text-center text-xs text-gray-400">加载中...</div>
                ) : configs.length === 0 ? (
                    <div className="rounded-xl bg-gray-50 p-4 text-center text-xs text-gray-400">还没有自定义模型配置</div>
                ) : (
                    <div className="space-y-2">
                        {configs.map((config) => (
                            <div key={config.id} className="rounded-xl border border-gray-100 p-3 text-sm shadow-sm">
                                <div className="flex items-start justify-between gap-2">
                                    <button type="button" onClick={() => editConfig(config)} className="min-w-0 flex-1 text-left">
                                        <div className="flex items-center gap-2">
                                            <span className="truncate font-medium text-gray-800">{config.display_name}</span>
                                            {config.is_default && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-600">默认</span>}
                                            {config.last_test_status === 'success' && <CheckCircle2 size={13} className="text-emerald-500" />}
                                        </div>
                                        <div className="mt-1 truncate text-xs text-gray-400">{config.model_planner}</div>
                                        <div className="mt-1 text-[11px] text-gray-400">{config.provider_type === 'anthropic' ? 'Anthropic Messages' : 'OpenAI-compatible'}</div>
                                        <div className="mt-1 truncate text-[11px] text-gray-400">{config.api_key_hint}</div>
                                    </button>
                                    <button type="button" onClick={() => void deleteConfig(config.id)} className="rounded-lg p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-500">
                                        <Trash2 size={14} />
                                    </button>
                                </div>
                                {!config.is_default && (
                                    <button type="button" onClick={() => void setDefault(config.id)} className="mt-2 text-xs text-[#7E57FF] hover:underline">
                                        设为默认
                                    </button>
                                )}
                                {config.last_test_status === 'failed' && config.last_test_error && (
                                    <div className="mt-2 rounded-lg bg-red-50 px-2 py-1 text-[11px] text-red-500">{config.last_test_error}</div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </section>

            <section className="rounded-[1.5rem] border border-purple-50 bg-white p-4 shadow-sm">
                <div className="mb-4">
                    <h2 className="text-sm font-bold text-gray-800">{form.id ? '编辑模型配置' : '新增模型配置'}</h2>
                    <p className="mt-1 text-xs text-gray-400">支持 OpenAI-compatible 与 Anthropic Messages，API Key 只加密保存，不会明文展示。</p>
                </div>
                <div className="space-y-3">
                    <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-xs text-gray-500">
                            展示名称
                            <input value={form.display_name} onChange={(e) => setForm((prev) => ({ ...prev, display_name: e.target.value }))} className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#7E57FF]" placeholder="我的模型服务" />
                        </label>
                        <label className="text-xs text-gray-500">
                            API 格式
                            <select value={form.provider_type} onChange={(e) => setForm((prev) => ({ ...prev, provider_type: e.target.value as AppLlmProviderType }))} className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-[#7E57FF]">
                                <option value="openai_compatible">OpenAI-compatible</option>
                                <option value="anthropic">Anthropic Messages</option>
                            </select>
                        </label>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-xs text-gray-500">
                            模型名称
                            <input value={form.model_planner} onChange={(e) => setForm((prev) => ({ ...prev, model_planner: e.target.value }))} className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#7E57FF]" placeholder={form.provider_type === 'anthropic' ? 'claude-sonnet-4-6' : 'gpt-4o-mini'} />
                        </label>
                        <label className="text-xs text-gray-500">
                            Base URL
                            <input value={form.base_url} onChange={(e) => setForm((prev) => ({ ...prev, base_url: e.target.value }))} className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#7E57FF]" placeholder={form.provider_type === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.example.com/v1'} />
                        </label>
                    </div>
                    <label className="block text-xs text-gray-500">
                        API Key{form.id ? '（留空表示不修改）' : ''}
                        <input type="password" value={form.api_key} onChange={(e) => setForm((prev) => ({ ...prev, api_key: e.target.value }))} className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#7E57FF]" placeholder="sk-..." />
                    </label>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-xs text-gray-500">
                            Writer 模型（可选）
                            <input value={form.model_writer} onChange={(e) => setForm((prev) => ({ ...prev, model_writer: e.target.value }))} className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#7E57FF]" placeholder="默认同模型名称" />
                        </label>
                        <label className="text-xs text-gray-500">
                            Vision 模型（可选）
                            <input value={form.model_vision_planner} onChange={(e) => setForm((prev) => ({ ...prev, model_vision_planner: e.target.value }))} className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#7E57FF]" placeholder="默认跟随系统视觉配置" />
                        </label>
                    </div>
                    <div className="flex flex-wrap gap-4 text-xs text-gray-600">
                        <label className="inline-flex items-center gap-2">
                            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm((prev) => ({ ...prev, enabled: e.target.checked }))} /> 启用
                        </label>
                        <label className="inline-flex items-center gap-2">
                            <input type="checkbox" checked={form.is_default} onChange={(e) => setForm((prev) => ({ ...prev, is_default: e.target.checked }))} /> 设为默认
                        </label>
                    </div>
                    <div className="flex justify-end gap-2 pt-2">
                        <button type="button" onClick={() => void testConfig()} disabled={testing} className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50">
                            {testing ? '测试中...' : '测试连接'}
                        </button>
                        <button type="button" onClick={() => void saveConfig()} disabled={saving} className="rounded-xl bg-[#7E57FF] px-4 py-2 text-sm text-white hover:bg-[#6c4ae0] disabled:opacity-50">
                            {saving ? '保存中...' : '保存配置'}
                        </button>
                    </div>
                </div>
            </section>
        </div>
    );
}
