import React from 'react';
import Header from '@/components/Header';
import { getNavItems } from '@/nav-items';
import { useNavigate, useLocation } from 'react-router-dom';
import { authStore } from '@/services/app-api';

export default function Layout({ children }: { children: React.ReactNode }) {
    const location = useLocation();
    const navigate = useNavigate();
    const isLoggedIn = authStore.isLoggedIn();
    const navItems = getNavItems(isLoggedIn);

    const isAuthPage = ['/login', '/register'].includes(location.pathname);
    const isAiChat = location.pathname === '/ai-chat';
    const isSubPage = [
        '/preferences',
        '/security-settings',
        '/blind-box',
        '/wheel',
        '/ai-chat'
    ].includes(location.pathname);
    const showBottomNav = !isAuthPage && !isSubPage;

    return (
        <div className="h-screen w-full bg-[#FFF9F2] flex flex-col overflow-hidden relative">
            {/* 顶部导航-仅在主页面显示 */}
            {showBottomNav && <Header />}

            {/* 主体内容区域-自动填充剩余空间 */}
            <main className="flex-1 min-h-0 relative flex flex-col overflow-hidden">
                <div className={`flex-1 flex flex-col h-full ${isAiChat ? 'w-full max-w-none' : 'container mx-auto px-4 max-w-2xl'}
                ${showBottomNav ? 'pt-2' : ''}`}>

                    {/* 页面自行控制滚动条 */}
                    <div
                        className={`flex-1 h-full w-full overflow-y-auto no-scrollbar ${showBottomNav ? 'pb-[calc(6rem+env(safe-area-inset-bottom))]' : ''
                            }`}
                    >
                        {children}
                    </div>
                </div>
            </main >

            {/* 底部tabbar-所有屏幕统一显示 */}
            {
                showBottomNav && (
                    <nav className="fixed bottom-0 left-0 right-0 relative bg-[#FFF9F2]/80 backdrop-blur-xl px-6 pt-3 flex justify-between items-center z-50 shadow-[0_-8px_24px_rgba(126,87,255,0.08)] pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
                        <div className="pointer-events-none absolute left-6 right-6 top-0 h-px bg-gradient-to-r from-transparent via-orange-200/40 to-transparent" />
                        {navItems.map((item) => {
                            const Icon = item.icon;
                            const isActive = location.pathname === item.to;
                            return (
                                <button
                                    key={item.to}
                                    onClick={() => navigate(item.to)}
                                    className={`flex flex-col items-center gap-1 transition-all ${isActive ? 'text-[#7E57FF] scale-110' : 'text-gray-400'}`}
                                >
                                    <Icon size={20} strokeWidth={isActive ? 2.5 : 2} />
                                    <span className="text-[10px] font-medium">{item.title}</span>
                                </button>
                            );
                        })}
                    </nav>
                )
            }
        </div >
    );
}
