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
        <div className="h-[100dvh] w-full bg-[#FFF9F2] flex flex-col md:flex-row overflow-hidden relative">
            {/* Desktop Sidebar (Only visible on md and up) */}
            {showBottomNav && (
                <aside className="hidden md:flex flex-col w-64 bg-[#FFF9F2] border-r border-orange-100 shadow-[4px_0_24px_rgba(126,87,255,0.04)] z-50 flex-shrink-0">
                    <div className="p-6">
                        <h1 className="text-2xl font-bold bg-gradient-to-r from-[#FF8C42] to-[#FF3B30] text-transparent bg-clip-text">
                            Smart Eats
                        </h1>
                    </div>
                    <nav className="flex-1 px-4 flex flex-col gap-2 mt-4">
                        {navItems.map((item) => {
                            const Icon = item.icon;
                            const isActive = location.pathname === item.to;
                            return (
                                <button
                                    key={item.to}
                                    onClick={() => navigate(item.to)}
                                    className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all ${isActive ? 'bg-orange-100/50 text-[#7E57FF] font-semibold shadow-sm' : 'text-gray-500 hover:bg-orange-50/50 hover:text-gray-800'}`}
                                >
                                    <Icon size={20} strokeWidth={isActive ? 2.5 : 2} />
                                    <span className="text-sm">{item.title}</span>
                                </button>
                            );
                        })}
                    </nav>
                </aside>
            )}

            <div className="flex-1 flex flex-col min-w-0 h-full relative">
                {/* 顶部导航-仅在主页面显示 */}
                {showBottomNav && <Header />}

                {/* 主体内容区域-自动填充剩余空间 */}
                <main className="flex-1 min-h-0 relative flex flex-col overflow-hidden">
                    <div className={`flex-1 flex flex-col h-full ${isAiChat ? 'w-full max-w-none' : 'container mx-auto px-4 md:px-8 max-w-2xl md:max-w-4xl lg:max-w-6xl xl:max-w-7xl'}
                    ${showBottomNav ? 'pt-2 md:pt-6' : ''}`}>

                        {/* 页面自行控制滚动条 */}
                        <div
                            className={`flex-1 h-full w-full overflow-y-auto no-scrollbar ${showBottomNav ? 'pb-[calc(6rem+env(safe-area-inset-bottom))] md:pb-8' : ''
                                }`}
                        >
                            {children}
                        </div>
                    </div>
                </main >

                {/* 底部tabbar-仅在小屏幕显示 */}
                {
                    showBottomNav && (
                        <nav className="md:hidden fixed bottom-0 left-0 right-0 relative bg-[#FFF9F2]/80 backdrop-blur-xl px-6 pt-3 flex justify-between items-center z-50 shadow-[0_-8px_24px_rgba(126,87,255,0.08)] pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
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
        </div >
    );
}
