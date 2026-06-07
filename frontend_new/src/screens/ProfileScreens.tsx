import React, { useEffect, useState } from 'react';
import { AlertCircle, Bell, BookOpen, ChevronRight, HelpCircle, LogOut, Settings, type LucideIcon } from 'lucide-react';
import { Header, ScreenScroll } from '../components/Layout';
import { appApi } from '../services/api';

export function ProfileScreen({ openSettings, logout }: { openSettings: () => void; logout: () => void }) {
  return (
    <ScreenScroll>
      <button onClick={openSettings} className="flex w-full items-center gap-4 text-left">
        <div className="grid h-16 w-16 place-items-center rounded-full bg-cyan-100 text-3xl">👨🏻</div>
        <span className="min-w-0 flex-1">
          <span className="block text-lg font-black">旅行爱好者</span>
          <span className="mt-1 block text-xs text-gray-400">traveler@example.com</span>
        </span>
        <ChevronRight size={18} />
      </button>
      <div className="mt-7 divide-y divide-gray-100">
        <ProfileRow Icon={BookOpen} label="我的收藏" />
        <ProfileRow Icon={Bell} label="消息通知" />
        <ProfileRow Icon={Settings} label="设置" onClick={openSettings} />
        <ProfileRow Icon={HelpCircle} label="帮助与反馈" />
        <ProfileRow Icon={AlertCircle} label="关于我们" />
      </div>
      <button onClick={logout} className="mt-8 flex items-center gap-2 text-sm font-bold text-red-500"><LogOut size={16} />退出登录</button>
    </ScreenScroll>
  );
}

function ProfileRow({ Icon, label, onClick }: { Icon: LucideIcon; label: string; onClick?: () => void }) {
  return <button onClick={onClick} className="flex w-full items-center gap-3 py-4 text-left"><Icon size={18} /><span className="flex-1 text-sm font-bold">{label}</span><ChevronRight size={16} /></button>;
}

export function SettingsScreen({ onBack, logout, openModelSettings, openEvalWorkbench }: { onBack: () => void; logout: () => void; openModelSettings: () => void; openEvalWorkbench?: () => void }) {
  const [showEvalEntry, setShowEvalEntry] = useState(false);

  useEffect(() => {
    let cancelled = false;
    appApi.evaluations
      .checkAccess()
      .then((data) => {
        if (!cancelled) setShowEvalEntry(data.allowed === true);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  return (
    <>
      <Header title="设置" onBack={onBack} />
      <div className="px-5">
        {showEvalEntry && openEvalWorkbench && (
          <button onClick={openEvalWorkbench} className="flex w-full items-center justify-between border-b border-gray-100 py-4 text-sm font-bold">
            <span>评测工作台</span>
            <ChevronRight size={16} />
          </button>
        )}
        {['个人信息', '账号与安全', '隐私设置', '通知设置', '清除缓存       12.5MB', '联系客服'].map((item) => <button key={item} className="flex w-full items-center justify-between border-b border-gray-100 py-4 text-sm font-bold"><span>{item}</span><ChevronRight size={16} /></button>)}
        <button onClick={openModelSettings} className="flex w-full items-center justify-between border-b border-gray-100 py-4 text-sm font-bold"><span>AI 模型设置</span><ChevronRight size={16} /></button>
        <button onClick={logout} className="mt-5 text-sm font-bold text-red-500">退出登录</button>
      </div>
    </>
  );
}
