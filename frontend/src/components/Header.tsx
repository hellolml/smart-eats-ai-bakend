import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Sparkles } from 'lucide-react';
import { appApi, authStore } from '@/services/app-api';

const Header = () => {
    const isLoggedIn = authStore.isLoggedIn();
    const [name, setName] = useState<string>('');

    useEffect(() => {
        const fetchProfile = async () => {
            if (!isLoggedIn) return;
            try {
                const profile = await appApi.me.get();
                setName(profile?.name || profile?.email || '');
            } catch (error) {
                console.error('fail to fetch profile for header:', error);
            }
        };
        fetchProfile();
    }, [isLoggedIn]);

    return (
        <header className="sticky top-0 z-50 w-full bg-white/80 backdrop-blur-md border-b border-purple-100">
            <div className="container mx-auto px-4 h-16 flex items-center justify-between">
                <Link to="/" className="flex items-center gap-2">
                    <div className="w-10 h-10 bg-[#7E57FF] rounded-xl flex items-center justify-center shadow-lg shadow-purple-200">
                        <Sparkles className="w-6 h-6 text-white" />
                    </div>
                    <span className="text-xl font-bold bg-gradient-to-r from-[#7E57FF] text-purple-400 bg-clip-text text-transparent">
                        吃点啥？
                    </span>
                </Link>

                <div className="flex items-center gap-4">
                    {isLoggedIn ? (
                        <Link
                            to="/profile"
                            className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-purple-50 hover:bg-purple-100 transition-colors"
                        >
                            <div className="w-6 h-6 rounded-full bg-[#7E57FF] text-white text-xs font-bold flex items-center justify-center md:w-8 md:h-8 md:text-sm">
                                {name ? name.charAt(0).toUpperCase() : 'U'}
                            </div>
                            <span className="text-sm font-medium text-gray-700 max-w-[100px] truncate hidden sm:block">
                                {name || 'User'}
                            </span>
                        </Link>
                    ) : (
                        <Link
                            to="/login"
                            className="px-3 py-1.5 rounded-full bg-[#7E57FF] text-white text-xs font-bold shadow-sm active:scale-90 transition-transform md:px-5 md:py-2 md:text-sm"
                        >
                            登录
                        </Link>
                    )}
                </div>
            </div>
        </header>
    );
};
export default Header;
