import React from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { appApi, AppGroupDecisionSession } from '@/services/app-api';

const DEFAULT_OPTIONS = ['火锅', '烧烤'];

const copyText = async (text: string) => {
  if (!text) return false;
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  if (typeof document !== 'undefined') {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  }
  return false;
};

const GroupDecisionCreate: React.FC = () => {
  const navigate = useNavigate();
  const [title, setTitle] = React.useState('今晚吃什么');
  const [city, setCity] = React.useState('');
  const [options, setOptions] = React.useState<string[]>(DEFAULT_OPTIONS);
  const [submitting, setSubmitting] = React.useState(false);
  const [created, setCreated] = React.useState<AppGroupDecisionSession | null>(null);

  const validOptions = React.useMemo(
    () => options.map((item) => item.trim()).filter(Boolean),
    [options]
  );

  const updateOption = (idx: number, value: string) => {
    setOptions((prev) => prev.map((item, i) => (i === idx ? value : item)));
  };

  const addOption = () => {
    setOptions((prev) => {
      if (prev.length >= 12) return prev;
      return [...prev, ''];
    });
  };

  const removeOption = (idx: number) => {
    setOptions((prev) => {
      if (prev.length <= 2) return prev;
      return prev.filter((_, i) => i !== idx);
    });
  };

  const builtShareUrl = React.useMemo(() => {
    if (!created) return '';
    const token = created.share_token || (() => {
      try {
        return new URL(created.share_url).searchParams.get('token') || '';
      } catch {
        return '';
      }
    })();
    const query = token ? `?token=${encodeURIComponent(token)}` : '';
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    return `${origin}/#/group-decision/${created.id}${query}`;
  }, [created]);

  const handleCopy = async () => {
    if (!builtShareUrl) return;
    try {
      const ok = await copyText(builtShareUrl);
      if (ok) toast.success('链接已复制');
      else toast.error('复制失败，请手动复制');
    } catch {
      toast.error('复制失败，请手动复制');
    }
  };

  const goVotePage = () => {
    if (!created) return;
    const token = created.share_token || (() => {
      try {
        return new URL(created.share_url).searchParams.get('token') || '';
      } catch {
        return '';
      }
    })();
    const query = token ? `?token=${encodeURIComponent(token)}` : '';
    navigate(`/group-decision/${created.id}${query}`);
  };

  const submit = async () => {
    if (validOptions.length < 2) {
      toast.error('至少填写 2 个候选项');
      return;
    }

    setSubmitting(true);
    try {
      const res = await appApi.groupDecisions.create({
        title: title.trim() || '今晚吃什么',
        city: city.trim() || undefined,
        options: validOptions.slice(0, 12).map((item) => ({
          title: item,
          item_type: 'restaurant'
        }))
      });
      setCreated(res);
      toast.success('群组决策已创建');
    } catch {
      toast.error('创建失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-xl mx-auto p-4 space-y-4">
      <div className="bg-white rounded-xl border p-4">
        <h1 className="text-lg font-semibold">发起群组决策</h1>
        <p className="text-xs text-gray-500 mt-1">把候选餐厅发给朋友投票，快速定今晚吃什么。</p>
      </div>

      <div className="bg-white rounded-xl border p-4 space-y-3">
        <label className="text-sm text-gray-600">标题</label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full border rounded-md px-3 py-2 text-sm"
          placeholder="今晚吃什么"
        />

        <label className="text-sm text-gray-600">城市（可选）</label>
        <input
          value={city}
          onChange={(e) => setCity(e.target.value)}
          className="w-full border rounded-md px-3 py-2 text-sm"
          placeholder="例如：上海"
        />
      </div>

      <div className="bg-white rounded-xl border p-4 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-700">候选项（2~12）</p>
          <button
            onClick={addOption}
            className="text-xs px-2 py-1 rounded-md bg-purple-50 text-[#7E57FF]"
            disabled={options.length >= 12}
          >
            + 添加
          </button>
        </div>

        {options.map((item, idx) => (
          <div key={idx} className="flex items-center gap-2">
            <input
              value={item}
              onChange={(e) => updateOption(idx, e.target.value)}
              className="flex-1 border rounded-md px-3 py-2 text-sm"
              placeholder={`候选项 ${idx + 1}`}
            />
            <button
              onClick={() => removeOption(idx)}
              disabled={options.length <= 2}
              className="text-xs px-2 py-2 rounded-md border text-gray-500 disabled:opacity-40"
            >
              删除
            </button>
          </div>
        ))}
      </div>

      <button
        onClick={submit}
        disabled={submitting}
        className="w-full py-3 rounded-xl bg-[#7E57FF] text-white font-bold text-sm disabled:opacity-60"
      >
        {submitting ? '创建中...' : '创建并生成分享链接'}
      </button>

      {created ? (
        <div className="bg-white rounded-xl border p-4 space-y-3">
          <p className="text-sm font-medium">创建成功</p>
          <p className="text-xs text-gray-500 break-all">{builtShareUrl || created.share_url}</p>
          <div className="flex gap-2">
            <button
              onClick={handleCopy}
              className="px-3 py-2 rounded-md bg-purple-50 text-[#7E57FF] text-sm"
            >
              复制链接
            </button>
            <button
              onClick={goVotePage}
              className="px-3 py-2 rounded-md bg-[#7E57FF] text-white text-sm"
            >
              去投票页
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default GroupDecisionCreate;
