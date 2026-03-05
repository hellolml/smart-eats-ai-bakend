import { appApi, AppGroupDecisionResult } from '@/services/app-api';
import React from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';

const GroupDecisionResult: React.FC = () => {
  const { sessionId = '' } = useParams();
  const [searchParams] = useSearchParams();
  const [data, setData] = React.useState<AppGroupDecisionResult | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [voterName, setVoterName] = React.useState('');

  const voterKey = React.useMemo(() => {
    const token = searchParams.get('token') || '';
    if (token) return token.slice(0, 16);
    return `guest_${Date.now()}`;
  }, [searchParams]);

  const load = React.useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const res = await appApi.groupDecisions.result(sessionId);
      setData(res);
    } catch (e) {
      toast.error('加载群组决策结果失败');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  React.useEffect(() => {
    load();
  }, [load]);

  const vote = async (itemId: string) => {
    if (!sessionId) return;
    if (!voterName.trim()) {
      toast.error('先填一下你的昵称');
      return;
    }
    try {
      await appApi.groupDecisions.vote({
        session_id: sessionId,
        item_id: itemId,
        voter_name: voterName.trim(),
        voter_key: `${voterKey}_${voterName.trim()}`.slice(0, 64)
      });
      toast.success('投票成功');
      await load();
    } catch {
      toast.error('投票失败，请稍后再试');
    }
  };

  if (loading) return <div className="p-4 text-sm text-gray-500">加载中...</div>;
  if (!data) return <div className="p-4 text-sm text-gray-500">没有找到该群组决策</div>;

  return (
    <div className="max-w-xl mx-auto p-4 space-y-4">
      <div className="bg-white rounded-xl border p-4">
        <h1 className="text-lg font-semibold">{data.title}</h1>
        <p className="text-xs text-gray-500 mt-1">总票数：{data.total_votes}</p>
        {data.winner ? (
          <div className="mt-3 text-sm">
            当前领先：<span className="font-medium">{data.winner.title}</span>（{data.winner.votes || 0}票）
          </div>
        ) : null}
      </div>

      <div className="bg-white rounded-xl border p-4 space-y-3">
        <label className="text-sm text-gray-600">你的昵称</label>
        <input
          value={voterName}
          onChange={(e) => setVoterName(e.target.value)}
          className="w-full border rounded-md px-3 py-2 text-sm"
          placeholder="例如：小王"
        />
      </div>

      <div className="bg-white rounded-xl border p-4 space-y-2">
        {data.items.map((item) => (
          <button
            key={item.id}
            onClick={() => vote(item.id)}
            className="w-full text-left border rounded-lg px-3 py-2 hover:bg-gray-50"
          >
            <div className="text-sm font-medium">{item.title}</div>
            <div className="text-xs text-gray-500 mt-1">{item.votes || 0} 票</div>
          </button>
        ))}
      </div>
    </div>
  );
};

export default GroupDecisionResult;
