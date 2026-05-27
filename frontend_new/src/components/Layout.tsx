import React from 'react';
import { motion } from 'framer-motion';
import { CalendarDays, ChevronLeft, Home, User, type LucideIcon } from 'lucide-react';
import type { Screen } from '../types';
import { cn } from '../lib/utils';

export function StatusBar() {
  return (
    <div className="absolute inset-x-0 top-0 z-40 flex h-11 items-center justify-between px-5 text-xs font-black text-black">
      <span>9:41</span>
      <span className="flex items-center gap-1">
        <span className="h-2 w-3 rounded-sm bg-black" />
        <span className="h-2 w-4 rounded-sm border border-black p-0.5"><span className="block h-full w-2 rounded-sm bg-black" /></span>
      </span>
    </div>
  );
}

export function Page({ active, children }: { active: boolean; children: React.ReactNode }) {
  return (
    <motion.section
      initial={false}
      animate={{ opacity: active ? 1 : 0, x: active ? 0 : 12 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
      className={cn('absolute inset-0 flex-col bg-white', active ? 'z-10 flex' : 'hidden')}
    >
      {children}
    </motion.section>
  );
}

export function Header({ title, subtitle, onBack, right }: { title: string; subtitle?: string; onBack?: () => void; right?: React.ReactNode }) {
  return (
    <header className="shrink-0 px-5 pb-3 pt-2">
      <div className="flex h-10 items-center justify-between">
        {onBack ? (
          <button onClick={onBack} className="flex h-9 w-9 items-center justify-center rounded-full" aria-label="返回"><ChevronLeft size={22} /></button>
        ) : <span className="h-9 w-9" />}
        <div className="min-w-0 flex-1 text-center">
          <h1 className="truncate text-base font-black">{title}</h1>
          {subtitle && <p className="mt-0.5 truncate text-[11px] font-medium text-gray-400">{subtitle}</p>}
        </div>
        <div className="flex h-9 w-9 items-center justify-center">{right}</div>
      </div>
    </header>
  );
}

export function ScreenScroll({ children }: { children: React.ReactNode }) {
  return <div className="h-full overflow-y-auto px-5 pb-24 pt-3 no-scrollbar">{children}</div>;
}

export function BottomAction({ label, onClick }: { label: string; onClick: () => void }) {
  return <div className="absolute inset-x-0 bottom-0 bg-white px-5 pb-7 pt-3"><button onClick={onClick} className="w-full rounded-full bg-black py-3 text-sm font-black text-white">{label}</button></div>;
}

export function BottomTabs({ active, visible, go }: { active: Screen; visible: boolean; go: (screen: Screen) => void }) {
  const tabs: Array<{ key: Screen; label: string; Icon: LucideIcon }> = [
    { key: 'home', label: '首页', Icon: Home },
    { key: 'plans', label: '我的计划', Icon: CalendarDays },
    { key: 'profile', label: '我的', Icon: User }
  ];
  return (
    <nav className={cn('absolute inset-x-0 bottom-0 z-40 grid h-20 grid-cols-3 items-start bg-white px-8 pt-3 transition-transform', visible ? 'translate-y-0' : 'translate-y-full')}>
      {tabs.map(({ key, label, Icon }) => (
        <button key={key} onClick={() => go(key)} className={cn('mx-auto flex h-12 w-16 flex-col items-center justify-center gap-1 text-[10px] font-bold', active === key ? 'text-blue-500' : 'text-black')}>
          <Icon size={20} />
          {label && <span>{label}</span>}
        </button>
      ))}
    </nav>
  );
}
