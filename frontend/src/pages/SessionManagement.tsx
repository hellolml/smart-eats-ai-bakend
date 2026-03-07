import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronLeft, RefreshCcw, Trash2, Link2, Link2Off } from 'lucide-react';
import toast from 'react-hot-toast';
import { ApiError, appApi } from '@/services/app-api';

type SessionItem = {
  id: string;
  status?: string;
  device_info?: string;
  ip?: string;
  last_ip?: string;
  last_seen_at?: string;
  created_at?: string;
  revoked_at?: string;
  revoke_reason?: string;
};

const SessionManagement: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = React.useState<SessionItem[]>([]);
  const [loading, setLoading] = React.useState(true);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await appApi.auth.listSessions();
      setItems((res.items || []) as SessionItem[]);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '加载会话失败');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const revokeSession = async (sessionId: string) => {
    try {
      await appApi.auth.revokeSession(sessionId);
      toast.success('已下线该设备');
      await load();
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '下线失败');
    }
  };

  const logoutAll = async () => {
    try {
      await appApi.auth.logoutAll();
      toast.success('已下线全部设备，请重新登录');
      navigate('/login');
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '操作失败');
    }
  };

  const bindGithub = async () => {
    try {
      const data = await appApi.auth.oauthStart('github');
      window.location.href = data.auth_url;
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '获取 GitHub 授权链接失败');
    }
  };

  const unbindGithub = async () => {
    try {
      const data = await appApi.auth.oauthUnbind('github');
      if (data.removed) toast.success('GitHub 解绑成功');
      else toast('当前未绑定 GitHub');
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '解绑失败');
    }
  };

  return (
    <div className="space-y-4 pb-8">
      <div className="flex items-center gap-3">
        <button onClick={() => navigate('/security-settings')} className="p-2 bg-white rounded-xl shadow-sm">
          <ChevronLeft size={18} />
        </button>
        <h2 className="text-lg font-semibold">会话管理</h2>
      </div>

      <div className="bg-white rounded-2xl border p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium">第三方登录</div>
          <button onClick={bindGithub} className="text-xs px-3 py-1 rounded-lg bg-black text-white flex items-center gap-1">
            <Link2 size={12} /> 绑定 GitHub
          </button>
        </div>
        <button onClick={unbindGithub} className="text-xs px-3 py-1 rounded-lg bg-gray-100 text-gray-700 flex items-center gap-1">
          <Link2Off size={12} /> 解绑 GitHub
        </button>
      </div>

      <div className="bg-white rounded-2xl border p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium">活跃设备</div>
          <button onClick={load} className="text-xs px-2 py-1 rounded-lg bg-gray-100 text-gray-700 flex items-center gap-1">
            <RefreshCcw size={12} /> 刷新
          </button>
        </div>

        {loading ? (
          <div className="text-sm text-gray-500">加载中...</div>
        ) : items.length === 0 ? (
          <div className="text-sm text-gray-500">暂无会话</div>
        ) : (
          <div className="space-y-2">
            {items.map((item) => (
              <div key={item.id} className="border rounded-xl p-3">
                <div className="text-xs text-gray-800">设备：{item.device_info || '未知设备'}</div>
                <div className="text-[11px] text-gray-500 mt-1">状态：{item.status || 'unknown'}</div>
                <div className="text-[11px] text-gray-500">IP：{item.last_ip || item.ip || '-'}</div>
                <div className="text-[11px] text-gray-500">最后活跃：{item.last_seen_at || '-'}</div>
                <button
                  onClick={() => revokeSession(item.id)}
                  className="mt-2 text-xs px-2 py-1 rounded-lg bg-red-50 text-red-600 flex items-center gap-1"
                >
                  <Trash2 size={12} /> 下线该设备
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <button onClick={logoutAll} className="w-full bg-red-500 text-white py-3 rounded-xl text-sm">
        下线所有设备
      </button>
    </div>
  );
};

export default SessionManagement;
