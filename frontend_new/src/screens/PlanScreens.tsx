import React from 'react';
import { QrCode, Search, Trash2 } from 'lucide-react';
import { BottomAction, Header, ScreenScroll } from '../components/Layout';
import { cn } from '../lib/utils';
import type { PlanInfo } from '../types';

export function PlansScreen({ plans, openPlan, deletePlan }: { plans: PlanInfo[]; openPlan: (plan: PlanInfo) => void; deletePlan: (plan: PlanInfo) => void }) {
  return (
    <ScreenScroll>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-black">我的计划</h1>
        <Search size={19} />
      </div>
      <div className="mt-5 flex gap-5 overflow-x-auto text-sm font-bold no-scrollbar">
        <span className="border-b-2 border-black pb-1">旅行计划</span>
      </div>
      <div className="mt-4 space-y-4">
        {!plans.length && (
          <div className="rounded-2xl bg-gray-50 px-5 py-12 text-center">
            <p className="text-sm font-black">还没有保存的计划</p>
            <p className="mt-2 text-xs text-gray-400">生成旅行计划并确认后，会展示在这里。</p>
          </div>
        )}
        {plans.map((item, index) => (
          <div key={item.id} className="flex w-full gap-3 text-left">
            <button onClick={() => openPlan(item)} className="flex min-w-0 flex-1 gap-3 text-left">
            <PlanThumb index={index} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-black">{item.title}</span>
              <span className="mt-1 block text-xs text-gray-400">{item.days.length} 天行程 | {item.sessionId ? 'Agent 生成' : '已保存'}</span>
              <span className="mt-1 block text-xs text-gray-400">{item.date}</span>
            </span>
            </button>
            <button onClick={() => deletePlan(item)} className="mt-3 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-red-50 text-red-500" aria-label="删除计划"><Trash2 size={16} /></button>
          </div>
        ))}
      </div>
    </ScreenScroll>
  );
}

function PlanThumb({ index }: { index: number }) {
  const styles = ['from-sky-300 to-blue-500', 'from-orange-200 to-rose-300', 'from-slate-200 to-slate-500', 'from-amber-100 to-orange-300'];
  return <span className={cn('h-16 w-16 shrink-0 rounded-xl bg-gradient-to-br', styles[index % styles.length])} />;
}

export function DetailScreen({ plan, onBack, onAdjust, onQr }: { plan: PlanInfo; onBack: () => void; onAdjust: () => void; onQr: () => void }) {
  const subtitle = [
    plan.basicInfo?.travelDays ? `${plan.basicInfo.travelDays}天` : `${plan.days.length}天`,
    plan.basicInfo?.travelPeople || ''
  ].filter(Boolean).join(' | ');
  return (
    <>
      <Header title={plan.title.replace('计划', '地图')} subtitle={subtitle || plan.date} onBack={onBack} />
      <div className="flex-1 overflow-y-auto px-5 pb-28 no-scrollbar">
        {plan.basicInfo && (
          <div className="mb-4 grid grid-cols-2 gap-2 text-xs">
            <InfoPill label="目的地" value={plan.basicInfo.destination || '-'} />
            <InfoPill label="出行时间" value={plan.basicInfo.travelDate || plan.date || '-'} />
            <InfoPill label="出行天数" value={plan.basicInfo.travelDays || `${plan.days.length}`} />
            <InfoPill label="出行人数" value={plan.basicInfo.travelPeople || '-'} />
          </div>
        )}
        <div className="rounded-2xl bg-amber-50 p-5">
          <p className="font-black">行程路线</p>
          <div className="mt-4 space-y-3 text-sm font-bold">
            {plan.days.map((day) => <p key={day.day}>{day.day}：{day.route}</p>)}
          </div>
        </div>
        {plan.sourceText && (
          <>
            <p className="mb-3 mt-6 font-black">AI 行程规划</p>
            <div className="whitespace-pre-wrap rounded-2xl bg-gray-50 p-4 text-xs leading-relaxed text-gray-600">{plan.sourceText}</div>
          </>
        )}
        <p className="mb-3 mt-6 font-black">地图概览</p>
        <div className="relative h-40 overflow-hidden rounded-2xl bg-[#eef2e8]">
          <div className="absolute inset-0 bg-[linear-gradient(90deg,transparent_24%,rgba(255,255,255,.65)_25%,transparent_26%),linear-gradient(0deg,transparent_24%,rgba(255,255,255,.65)_25%,transparent_26%)] bg-[length:44px_44px]" />
          {[['left-16 top-16 bg-blue-500'], ['left-28 top-24 bg-red-500'], ['right-14 top-12 bg-blue-500'], ['right-8 bottom-8 bg-red-500'], ['left-10 bottom-8 bg-blue-500']].map(([cls], i) => <span key={i} className={cn('absolute h-4 w-4 rounded-full border-2 border-white shadow', cls)} />)}
        </div>
      </div>
      <div className="absolute inset-x-0 bottom-0 grid grid-cols-2 gap-3 bg-white px-5 pb-8 pt-3">
        <button onClick={onAdjust} className="rounded-full bg-black py-3 text-sm font-bold text-white">调整计划</button>
        <button onClick={onQr} className="rounded-full bg-black py-3 text-sm font-bold text-white"><QrCode size={15} className="mr-1 inline" />高德二维码</button>
      </div>
    </>
  );
}

function InfoPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-gray-50 px-3 py-2">
      <p className="text-[10px] font-bold text-gray-400">{label}</p>
      <p className="mt-1 truncate font-black text-gray-800">{value}</p>
    </div>
  );
}

export function QrScreen({ plan, onBack }: { plan: PlanInfo; onBack: () => void }) {
  return (
    <>
      <Header title="地图二维码详情" onBack={onBack} />
      <div className="flex-1 px-5 pt-4">
        <div className="grid place-items-center rounded-2xl bg-gray-50 px-6 py-12">
          <p className="text-xl font-black">{plan.title.replace('计划', '地图')}</p>
          <p className="mt-2 text-xs text-gray-400">扫码查看完整地图</p>
          {plan.qrCodeUrl ? <img src={plan.qrCodeUrl} alt="地图二维码" className="mt-9 h-44 w-44 object-contain" /> : <QrArt />}
        </div>
        <div className="mt-5 rounded-2xl bg-gray-50 p-5">
          <p className="font-black">使用说明</p>
          <ol className="mt-3 space-y-2 text-xs leading-relaxed text-gray-500">
            <li>1. 扫描二维码可查看完整行程地图</li>
            <li>2. 可在地图中查看景点位置和路线</li>
            <li>3. 支持导航和路线信息规划</li>
          </ol>
        </div>
      </div>
      <BottomAction label="保存图片" onClick={onBack} />
    </>
  );
}

function QrArt() {
  return (
    <svg viewBox="0 0 120 120" className="mt-9 h-44 w-44 text-black">
      <rect width="120" height="120" rx="8" fill="white" />
      <g fill="currentColor">
        <rect x="10" y="10" width="28" height="28" /><rect x="16" y="16" width="16" height="16" fill="white" /><rect x="20" y="20" width="8" height="8" />
        <rect x="82" y="10" width="28" height="28" /><rect x="88" y="16" width="16" height="16" fill="white" /><rect x="92" y="20" width="8" height="8" />
        <rect x="10" y="82" width="28" height="28" /><rect x="16" y="88" width="16" height="16" fill="white" /><rect x="20" y="92" width="8" height="8" />
        <path d="M48 11h8v8h-8zM61 18h15v8H61zM46 34h9v8h-9zM64 38h10v10H64zM84 48h8v8h-8zM100 48h8v18h-8zM47 57h15v8H47zM70 60h21v8H70zM48 74h9v9h-9zM62 82h17v8H62zM87 78h10v10H87zM101 93h10v9h-10zM50 101h28v8H50z" />
      </g>
    </svg>
  );
}
