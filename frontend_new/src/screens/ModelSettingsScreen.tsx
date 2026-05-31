import React, { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ArrowLeft, Check, ChevronRight, Cpu, Eye, EyeOff, Link2, Loader2, Pencil, Plus, RefreshCw, Star, Trash2 } from 'lucide-react';
import { Header, ScreenScroll } from '../components/Layout';
import { appApi, authStore } from '../services/api';
import type { ModelConfig, ModelConfigInput, ProviderType } from '../types';
import { cn, errorMessage } from '../lib/utils';

function handleAuthFailure(error: unknown): boolean {
  if (authStore.isAuthError(error)) {
    toast.error('登录状态已失效，请重新登录');
    authStore.clear();
    return true;
  }
  return false;
}

export function ModelSettingsScreen({ onBack }: { onBack: () => void }) {
  const [configs, setConfigs] = useState<ModelConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string>('');
  const [deleting, setDeleting] = useState<string>('');

  // Form state
  const [displayName, setDisplayName] = useState('');
  const [providerType, setProviderType] = useState<ProviderType>('openai_compatible');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [modelPlanner, setModelPlanner] = useState('');
  const [modelWriter, setModelWriter] = useState('');
  const [modelVision, setModelVision] = useState('');

  const loadConfigs = async () => {
    try {
      setLoading(true);
      const data = await appApi.modelConfig.list();
      setConfigs(data);
    } catch (error) {
      if (!handleAuthFailure(error)) {
        toast.error(`加载模型配置失败：${errorMessage(error)}`);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadConfigs();
  }, []);

  const resetForm = () => {
    setDisplayName('');
    setProviderType('openai_compatible');
    setBaseUrl('');
    setApiKey('');
    setShowApiKey(false);
    setModelPlanner('');
    setModelWriter('');
    setModelVision('');
  };

  const openEdit = (config: ModelConfig) => {
    setEditing(config);
    setDisplayName(config.display_name);
    setProviderType(config.provider_type as ProviderType);
    setBaseUrl(config.base_url);
    setApiKey('');
    setShowApiKey(false);
    setModelPlanner(config.model_planner);
    setModelWriter(config.model_writer || '');
    setModelVision(config.model_vision_planner || '');
    setShowForm(true);
  };

  const openCreate = () => {
    setEditing(null);
    resetForm();
    setShowForm(true);
  };

  const buildPayload = (): ModelConfigInput => {
    const payload: ModelConfigInput = {
      display_name: displayName.trim(),
      provider_type: providerType,
      base_url: baseUrl.trim(),
      model_planner: modelPlanner.trim(),
    };
    if (modelWriter.trim()) payload.model_writer = modelWriter.trim();
    if (modelVision.trim()) payload.model_vision_planner = modelVision.trim();
    if (editing) {
      if (apiKey.trim()) payload.api_key = apiKey.trim();
    } else {
      payload.api_key = apiKey.trim();
    }
    return payload;
  };

  const handleSave = async () => {
    if (!displayName.trim() || !modelPlanner.trim() || !baseUrl.trim()) {
      toast.error('请填写模型名称、Base URL 和规划模型');
      return;
    }
    if (!editing && !apiKey.trim()) {
      toast.error('请填写 API Key');
      return;
    }
    try {
      setSaving(true);
      const payload = buildPayload();
      if (editing) {
        await appApi.modelConfig.update(editing.id, payload);
        toast.success('模型配置已更新');
      } else {
        await appApi.modelConfig.create(payload);
        toast.success('模型配置已创建');
      }
      setShowForm(false);
      resetForm();
      await loadConfigs();
    } catch (error) {
      if (!handleAuthFailure(error)) {
        toast.error(errorMessage(error));
      }
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (configId: string) => {
    try {
      setTesting(configId);
      const result = await appApi.modelConfig.test({ config_id: configId });
      if (result.status === 'success') {
        toast.success('连接测试成功');
      } else {
        toast.error(result.error || '连接测试失败');
      }
      await loadConfigs();
    } catch (error) {
      if (!handleAuthFailure(error)) {
        toast.error(errorMessage(error));
      }
    } finally {
      setTesting('');
    }
  };

  const handleSetDefault = async (configId: string) => {
    try {
      await appApi.modelConfig.setDefault(configId);
      toast.success('已设为默认模型');
      await loadConfigs();
    } catch (error) {
      if (!handleAuthFailure(error)) {
        toast.error(errorMessage(error));
      }
    }
  };

  const handleDelete = async (configId: string) => {
    if (!window.confirm('确定要删除这个模型配置吗？')) return;
    try {
      setDeleting(configId);
      await appApi.modelConfig.remove(configId);
      toast.success('模型配置已删除');
      await loadConfigs();
    } catch (error) {
      if (!handleAuthFailure(error)) {
        toast.error(errorMessage(error));
      }
    } finally {
      setDeleting('');
    }
  };

  const providerName = (t: string) => t === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容';

  const testStatusBadge = (config: ModelConfig) => {
    if (!config.last_test_status) return null;
    const ok = config.last_test_status === 'success';
    return (
      <span className={cn('ml-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold', ok ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600')}>
        <span className={cn('h-1.5 w-1.5 rounded-full', ok ? 'bg-green-500' : 'bg-red-500')} />
        {ok ? '已连接' : '失败'}
      </span>
    );
  };

  return (
    <>
      <Header title="AI 模型设置" onBack={onBack} />
      <ScreenScroll>
        {/* Hint */}
        <div className="mb-4 rounded-xl bg-blue-50 p-3 text-xs text-blue-700">
          配置你的 AI 模型，支持 OpenAI 兼容和 Anthropic API。设为默认后会随每次对话自动使用。
        </div>

        {/* Config list */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 size={24} className="animate-spin text-gray-300" />
          </div>
        ) : configs.length === 0 ? (
          <div className="py-12 text-center text-sm text-gray-400">暂无模型配置，点击下方按钮添加</div>
        ) : (
          <div className="divide-y divide-gray-100 rounded-xl bg-white shadow-sm">
            {configs.map((config) => (
              <div key={config.id} className="px-4 py-3">
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      {config.is_default && <Star size={14} className="shrink-0 text-amber-400" fill="currentColor" />}
                      <span className="truncate text-sm font-bold">{config.display_name}</span>
                      {testStatusBadge(config)}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-gray-400">
                      <span className="inline-flex items-center gap-1 rounded bg-gray-100 px-1.5 py-0.5 font-medium">{providerName(config.provider_type)}</span>
                      <span>{config.model_planner}</span>
                    </div>
                    <div className="mt-0.5 text-[10px] text-gray-300 truncate">{config.base_url}</div>
                  </div>
                </div>
                <div className="mt-2 flex items-center gap-1">
                  <button
                    disabled={config.is_default}
                    onClick={() => handleSetDefault(config.id)}
                    className={cn('rounded px-2 py-1 text-[10px] font-bold', config.is_default ? 'text-amber-500' : 'text-gray-400 hover:text-gray-600')}
                  >
                    {config.is_default ? '当前默认' : '设为默认'}
                  </button>
                  <button
                    disabled={testing === config.id}
                    onClick={() => handleTest(config.id)}
                    className="flex items-center gap-1 rounded px-2 py-1 text-[10px] font-bold text-blue-500 hover:text-blue-600"
                  >
                    {testing === config.id ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                    测试
                  </button>
                  <button onClick={() => openEdit(config)} className="rounded px-2 py-1 text-[10px] font-bold text-gray-400 hover:text-gray-600"><Pencil size={12} /></button>
                  <button
                    disabled={deleting === config.id}
                    onClick={() => handleDelete(config.id)}
                    className="rounded px-2 py-1 text-[10px] font-bold text-red-400 hover:text-red-600"
                  >
                    {deleting === config.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Add button */}
        <button onClick={openCreate} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-200 py-4 text-sm font-bold text-gray-400 transition-colors hover:border-blue-300 hover:text-blue-500">
          <Plus size={18} />
          添加模型配置
        </button>
      </ScreenScroll>

      {/* Form modal */}
      {showForm && (
        <div className="absolute inset-0 z-50 flex flex-col bg-white">
          <header className="flex h-12 shrink-0 items-center gap-3 border-b border-gray-100 px-4">
            <button onClick={() => { setShowForm(false); resetForm(); }} className="flex h-9 w-9 items-center justify-center rounded-full"><ArrowLeft size={20} /></button>
            <h2 className="flex-1 text-base font-black">{editing ? '编辑模型' : '添加模型'}</h2>
            <button onClick={handleSave} disabled={saving} className="flex items-center gap-1 rounded-lg bg-black px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50">
              {saving && <Loader2 size={12} className="animate-spin" />}
              保存
            </button>
          </header>
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            {/* Display Name */}
            <FormField label="配置名称" required>
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="例如：我的 GPT-5" className="form-input" />
            </FormField>

            {/* Provider Type */}
            <FormField label="API 类型" required>
              <div className="flex gap-2">
                {(['openai_compatible', 'anthropic'] as ProviderType[]).map((t) => (
                  <button
                    key={t}
                    onClick={() => setProviderType(t)}
                    className={cn('flex-1 rounded-lg border py-2 text-center text-xs font-bold transition-colors', providerType === t ? 'border-black bg-black text-white' : 'border-gray-200 text-gray-500')}
                  >
                    {providerName(t)}
                  </button>
                ))}
              </div>
            </FormField>

            {/* Base URL */}
            <FormField label="Base URL" required>
              <div className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-3 py-2">
                <Link2 size={14} className="text-gray-300 shrink-0" />
                <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" className="w-full bg-transparent text-sm outline-none" />
              </div>
              <p className="mt-1 text-[10px] text-gray-400">必须以 http:// 或 https:// 开头</p>
            </FormField>

            {/* API Key */}
            <FormField label="API Key" required={!editing}>
              <div className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white">
                <input
                  type={showApiKey ? 'text' : 'password'}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={editing ? '留空则不修改' : 'sk-...'}
                  className="w-full bg-transparent px-3 py-2 text-sm outline-none"
                />
                <button onClick={() => setShowApiKey(!showApiKey)} className="px-3 py-2 text-gray-400">
                  {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              {editing && <p className="mt-1 text-[10px] text-gray-400">留空不修改已有 Key</p>}
            </FormField>

            {/* Model Planner */}
            <FormField label="规划模型" required>
              <input value={modelPlanner} onChange={(e) => setModelPlanner(e.target.value)} placeholder="例如：gpt-4o-mini" className="form-input" />
            </FormField>

            {/* Model Writer */}
            <FormField label="写作模型">
              <input value={modelWriter} onChange={(e) => setModelWriter(e.target.value)} placeholder="留空则与规划模型相同" className="form-input" />
            </FormField>

            {/* Model Vision */}
            <FormField label="视觉模型">
              <input value={modelVision} onChange={(e) => setModelVision(e.target.value)} placeholder="可选，用于图片识别的模型" className="form-input" />
            </FormField>
          </div>
        </div>
      )}
    </>
  );
}

function FormField({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-bold text-gray-600">
        {label}
        {required && <span className="ml-0.5 text-red-400">*</span>}
      </span>
      {children}
    </label>
  );
}
