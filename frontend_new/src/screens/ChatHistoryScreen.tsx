import React from 'react';
import { Clock3, Loader2, MessageCircle, Search, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { Header } from '../components/Layout';
import { type ChatSessionSummary, appApi, authStore } from '../services/api';
import type { AgentMode, Message } from '../types';
import { cn, errorMessage, message } from '../lib/utils';

type HistorySession = ChatSessionSummary;

export function ChatHistoryScreen(props: {
  active: boolean;
  mode: AgentMode;
  onBack: () => void;
  openSession: (payload: { session: HistorySession; mode: AgentMode; messages: Message[] }) => void;
  onAuthExpired: () => void;
}) {
  const onAuthExpiredRef = React.useRef(props.onAuthExpired);
  const openLoadedSessionRef = React.useRef(props.openSession);
  const [sessions, setSessions] = React.useState<HistorySession[]>([]);
  const [query, setQuery] = React.useState('');
  const [offset, setOffset] = React.useState(0);
  const [hasMore, setHasMore] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [openingId, setOpeningId] = React.useState('');
  const [deletingId, setDeletingId] = React.useState('');
  const limit = 20;
  const scene = props.mode === 'travel' ? 'travel_planner' : 'eat';
  const title = props.mode === 'travel' ? '旅行历史' : '美食历史';

  React.useEffect(() => {
    onAuthExpiredRef.current = props.onAuthExpired;
    openLoadedSessionRef.current = props.openSession;
  }, [props.onAuthExpired, props.openSession]);

  const loadSessions = React.useCallback(async (nextOffset = 0, append = false) => {
    if (!props.active) return;
    setLoading(true);
    try {
      const result = await appApi.chat.listSessions({ limit, offset: nextOffset, q: query.trim() || undefined, scene });
      setSessions((prev) => append ? [...prev, ...result.sessions] : result.sessions);
      setOffset(result.offset + result.sessions.length);
      setHasMore(result.sessions.length >= result.limit);
    } catch (error) {
      if (authStore.isAuthError(error)) {
        onAuthExpiredRef.current();
        return;
      }
      toast.error(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [query, props.active, scene]);

  React.useEffect(() => {
    if (!props.active) return;
    void loadSessions(0, false);
  }, [props.active, loadSessions]);

  const openSession = async (session: HistorySession) => {
    setOpeningId(session.session_id);
    try {
      const result = await appApi.chat.listMessages(session.session_id, { limit: 200, offset: 0 });
      const mapped = result.messages
        .filter((item) => item.role === 'user' || item.role === 'assistant')
        .map((item) => ({
          ...message(item.role === 'user' ? 'user' : 'assistant', item.content || ''),
          id: item.id,
          createdAt: item.created_at ? new Date(item.created_at).getTime() : Date.now()
        }));
      openLoadedSessionRef.current({
        session,
        mode: props.mode,
        messages: mapped.length ? mapped : [message('assistant', '这次会话暂时没有可展示的消息。')]
      });
    } catch (error) {
      if (authStore.isAuthError(error)) {
        onAuthExpiredRef.current();
        return;
      }
      toast.error(errorMessage(error));
    } finally {
      setOpeningId('');
    }
  };

  const deleteSession = async (session: HistorySession) => {
    const ok = window.confirm(`确定删除「${session.title || '未命名会话'}」吗？`);
    if (!ok) return;
    setDeletingId(session.session_id);
    setSessions((prev) => prev.filter((item) => item.session_id !== session.session_id));
    try {
      await appApi.chat.deleteSession(session.session_id);
      toast.success('会话已删除');
    } catch (error) {
      void loadSessions(0, false);
      if (authStore.isAuthError(error)) {
        onAuthExpiredRef.current();
        return;
      }
      toast.error(errorMessage(error));
    } finally {
      setDeletingId('');
    }
  };

  return (
    <>
      <Header title={title} subtitle="查看、切换或删除你的 Agent 对话" onBack={props.onBack} />
      <div className="shrink-0 px-5 pb-3">
        <label className="flex h-11 items-center gap-2 rounded-2xl bg-gray-50 px-3 text-sm text-gray-500">
          <Search size={17} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索会话标题"
            className="min-w-0 flex-1 bg-transparent text-gray-900 outline-none placeholder:text-gray-400"
          />
        </label>
      </div>
      <div className="flex-1 overflow-y-auto px-5 pb-8 no-scrollbar">
        {loading && sessions.length === 0 ? (
          <div className="space-y-3">
            {Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-20 animate-pulse rounded-2xl bg-gray-50" />)}
          </div>
        ) : sessions.length === 0 ? (
          <div className="grid h-full place-items-center py-16 text-center">
            <div>
              <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-gray-50 text-gray-500"><MessageCircle size={24} /></div>
              <p className="mt-4 text-sm font-black">暂无历史会话</p>
              <p className="mt-1 text-xs text-gray-400">{props.mode === 'travel' ? '新的旅行计划对话会出现在这里。' : '新的美食对话会出现在这里。'}</p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {sessions.map((session) => (
              <button
                key={session.session_id}
                onClick={() => void openSession(session)}
                className="flex w-full items-center gap-3 rounded-2xl border border-gray-100 bg-white p-3 text-left shadow-sm transition active:scale-[0.99]"
              >
                  <div className={cn('grid h-11 w-11 shrink-0 place-items-center rounded-2xl', 'bg-orange-50 text-orange-500')}>
                  {openingId === session.session_id ? <Loader2 size={19} className="animate-spin" /> : <Clock3 size={19} />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-black text-gray-950">{session.title || '未命名会话'}</p>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-gray-400">
                    <span className="rounded-full bg-gray-50 px-2 py-0.5 font-bold text-gray-500">{sceneLabel(session)}</span>
                    <span>{relativeTime(session.created_at)}</span>
                  </div>
                </div>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(event) => {
                    event.stopPropagation();
                    void deleteSession(session);
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') return;
                    event.preventDefault();
                    event.stopPropagation();
                    void deleteSession(session);
                  }}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-gray-400"
                  aria-label="删除会话"
                >
                  {deletingId === session.session_id ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
                </span>
              </button>
            ))}
            {hasMore && (
              <button onClick={() => void loadSessions(offset, true)} disabled={loading} className="w-full rounded-full bg-gray-100 py-3 text-xs font-black text-gray-700 disabled:opacity-60">
                {loading ? '加载中...' : '加载更多'}
              </button>
            )}
          </div>
        )}
      </div>
    </>
  );
}

function sceneLabel(session: HistorySession) {
  if (session.scene === 'travel_planner') return '旅行';
  if (session.scene === 'eat') return '美食';
  return '会话';
}

function relativeTime(value: string) {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return '刚刚';
  const diff = Math.max(0, Date.now() - time);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return '刚刚';
  if (diff < hour) return `${Math.floor(diff / minute)}分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)}小时前`;
  if (diff < 7 * day) return `${Math.floor(diff / day)}天前`;
  return new Date(value).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}
