import React from 'react';
import { ChevronRight, MoreHorizontal, Plane, User, type LucideIcon } from 'lucide-react';
import { ScreenScroll } from '../components/Layout';
import { cn } from '../lib/utils';

export function HomeScreen({ openCreateTravel, startEat, openProfile }: { openCreateTravel: () => void; startEat: () => void; openProfile: () => void }) {
  return (
    <ScreenScroll>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[28px] font-black tracking-tight">计划助手</h1>
          <p className="mt-2 text-sm font-medium text-gray-500">你的专属计划 AI 助手</p>
        </div>
        <div className="flex gap-3">
          <button onClick={openProfile} className="flex h-9 w-9 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-gray-100" aria-label="个人"><User size={18} /></button>
        </div>
      </div>

      <h2 className="mb-3 mt-8 text-sm font-black">快速入口</h2>
      <button onClick={startEat} className="flex w-full items-center justify-between rounded-2xl bg-orange-50 p-4 text-left">
        <span>
          <span className="flex items-center gap-2 text-sm font-black"><span className="h-5 w-5 rounded-full bg-orange-400 ring-4 ring-orange-100" />快决策 · 吃什么</span>
          <span className="mt-2 block text-xs text-gray-400">不知道吃什么？来问问我</span>
        </span>
        <span className="grid h-14 w-16 place-items-center rounded-full bg-gradient-to-br from-orange-200 to-orange-400 text-2xl">🍛</span>
      </button>

      <h2 className="mb-3 mt-8 text-sm font-black">更多计划</h2>
      <div className="space-y-3">
        <HomePlanRow Icon={Plane} color="blue" title="旅行计划" desc="智能生成行程安排" onClick={openCreateTravel} />
        <HomePlanRow Icon={MoreHorizontal} color="gray" title="更多计划" desc="敬请期待更多类型" />
      </div>
    </ScreenScroll>
  );
}

function HomePlanRow({ Icon, color, title, desc, onClick }: { Icon: LucideIcon; color: 'blue' | 'gray'; title: string; desc: string; onClick?: () => void }) {
  const palette = {
    blue: 'bg-blue-100 text-blue-600',
    gray: 'bg-gray-100 text-gray-500'
  }[color];
  return (
    <button onClick={onClick} className="flex w-full items-center gap-3 rounded-2xl bg-gray-50 p-4 text-left">
      <span className={cn('grid h-10 w-10 place-items-center rounded-xl', palette)}><Icon size={20} /></span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-black">{title}</span>
        <span className="mt-1 block text-xs text-gray-400">{desc}</span>
      </span>
      <ChevronRight size={17} />
    </button>
  );
}
