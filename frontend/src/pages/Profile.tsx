import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    ChevronRight, LogOut, Settings2, Shield, Pencil, Check,
    Camera, HelpCircle, Info, Clock,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { motion, AnimatePresence } from 'framer-motion';
import { ApiError, AppProfile, appApi, authStore } from '@/services/app-api';

const Profile = () => {
    const navigate = useNavigate();
    const [isLoggedIn, setIsLoggedIn] = useState<boolean>(authStore.isLoggedIn());
    const [profile, setProfile] = useState<AppProfile | null>(null);
    const [isEditingName, setIsEditingName] = useState(false);
    const [tempName, setTempName] = useState('');
    useEffect(() => {
        const loginStatus = authStore.isLoggedIn();
        setIsLoggedIn(loginStatus);
        if (!loginStatus) {
            setProfile(null);
            setTempName('');
            return;
        }

        const fetchProfile = async () => {
            try {
                const data = await appApi.me.get();
                setProfile(data);
                setTempName(data?.name || '');
            } catch (error) {
                console.error('Failed to fetch profile:', error);
            }
        };
        fetchProfile();
    }, []);

    const handleUpdateName = async () => {
        if (!isLoggedIn) {
            toast.error('请先登录');
            navigate('/login');
            return;
        }
        if (!tempName.trim()) {
            toast.error('昵称不能为空');
            return;
        }
        try {
            const updated = await appApi.me.update({ name: tempName.trim() });
            setProfile((prev) => ({ ...(prev || {}), ...updated } as AppProfile));
            setIsEditingName(false);
            toast.success("昵称已更新");
        } catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || "更新失败");
                return;
            }
            toast.error("更新失败");
        }
    };

    const handleLogout = async () => {
        try {
            await appApi.auth.logout();
            toast.success('已退出登录');
            navigate('/login');
        } catch (error) {
            toast.error('退出失败，请稍后重试');
        }
    };

    const handleChangeAvatar = async () => {
        if (!isLoggedIn) {
            toast.error('请先登录');
            navigate('/login');
            return;
        }
        const randomAvatar = `https://api.dicebear.com/9.x/adventurer/svg?seed=${Date.now()}`;
        try {
            const updated = await appApi.me.update({ avatar: randomAvatar });
            setProfile((prev) => ({ ...(prev || {}), ...updated } as AppProfile));
            toast.success('头像已更新');
        }
        catch (error) {
            toast.error('更新失败');
        }
    };

    const menuItems = [
        {
            title: '偏好设置',
            desc: '口味、忌口与习惯',
            icon: Settings2,
            color: 'text-orange-500',
            bgColor: 'bg-orange-50',
            path: '/preferences'
        },
        {
            title: '安全设置',
            desc: '修改密码与账号保护',
            icon: Shield,
            color: 'text-purple-500',
            bgColor: 'bg-purple-50',
            path: '/security-settings'
        },
        {
            title: '帮助中心',
            desc: '常见问题与指南',
            icon: HelpCircle,
            color: 'text-blue-500',
            bgColor: 'bg-blue-50',
            path: '#'
        },
        {
            title: '关于',
            desc: '版本 v0.0.1',
            icon: Info,
            color: 'text-gray-500',
            bgColor: 'bg-gray-50',
            path: '#'
        }
    ];

    return (
        <div className="h-full flex flex-col overflow-hidden pb-20 md:pb-4 animate-in fade-in duration-500 relative">
            {/*顶部背景装饰*/}
            <div className="absolute top-0 left-0 right-0 h-24 bg-gradient-to-b from-[#7E57FF]/10 to-transparent -z-10" />

            {/*个人资料头部*/}
            <motion.section
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex-shrink-0 pt-4 pb-6 px-6 flex items-center gap-4 bg-white shadow-sm"
            >
                {/* 头像区域*/}
                <div className="relative flex-shrink-0">
                    <div className="w-16 h-16 rounded-[1.5rem] bg-gradient-to-tr from-[#7E57FF] to-purple-300 p-0.5 shadow-lg shadow-purple-100">
                        <div className="w-full h-full rounded-[1.4rem] bg-white flex items-center justify-center overflow-hidden">
                            <img
                                src={
                                    profile?.avatar || ''}
                                alt="Avatar"
                                className="w-full h-full object-cover" />
                        </div>
                    </div>

                    <button onClick={handleChangeAvatar}
                        className="absolute -bottom-1 -right-1 w-6 h-6 bg-white rounded-lg shadow-md flex items-center justify-center text-[#7E57FF] border border-purple-50 active:scale-90 transition-transform">
                        <Camera size={12} />
                    </button>
                </div>
                {/* 昵称区域 */}
                <div className="flex flex-col items-start gap-1 flex-1 min-w-0">
                    <AnimatePresence mode="wait">
                        {isEditingName ? (
                            <motion.div
                                initial={{ opacity: 0, y: -10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                className="flex items-center gap-2 w-full"
                            >
                                <input
                                    autoFocus
                                    value={tempName}
                                    onChange={(e) => setTempName(e.target.value)}
                                    className="bg-white border border-purple-100 outline-none text-base font-black text-gray-800 w-full px-2 py-0.5 rounded-lg focus-ring-2 focus:ring-purple-100"
                                />
                                <button onClick={handleUpdateName}
                                    className="text-green-500 p-1 bg-green-50 rounded-lg shadow-sm flex-shrink-0">
                                    <Check size={14} />
                                </button >

                                {/* <button
                                    onClick={() => {
                                        setIsEditingName(false);
                                        setTempName(profile?.name);
                                    }}
                                    className="text-red-400 p-1">
                                    <X size={14} />
                                </button > */}
                            </motion.div>) : (
                            <motion.div
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 1 }}
                                className="flex items-center gap-2">
                                <h2 className="text-lg font-black text-gray-800 truncate">
                                    {profile?.name || '美食探索者'}
                                </h2>
                                <button
                                    onClick={() => setIsEditingName(true)}
                                    className="p-1 text-gray-300 hover:text-[#7E57FF] transition-colors"
                                >
                                    <Pencil size={12} />
                                </button>
                            </motion.div>
                        )}
                    </AnimatePresence>
                    <div className="flex flex-col gap-0.5">
                        <div className="flex items-center gap-2">

                            <span className="text-[9px] text-gray-400 font-medium">
                                ID: {profile?.id || '88888888'}
                            </span>
                        </div>
                        <div className="flex items-center gap-1 text-[9px] text-gray-400 font-medium">
                            <Clock size={9} className="text-purple-300" />
                            <span>已加入{profile?.joined_days ?? 1}天</span>
                        </div>

                    </div>
                </div >
            </motion.section>
            {/* 功能列表*/}
            <div className="flex-1 overflow-hidden mt-2">
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="bg-white border-y border-purple-50 shadow-sm overflow-hidden divide-y divide-gray-50"
                >
                    {menuItems.map((item, idx) => (
                        <button
                            key={idx}
                            onClick={() => item.path !== '#' && navigate(item.path)}
                            className="w-full px-0 py-2.5 flex items-center justify-between hover:bg-gray-50 transition-all group active:bg-purple-50/30"
                        >
                            <div className="flex items-center gap-3 px-6">
                                <div className={`${item.bgColor} ${item.color} p-2 rounded-lg group-hover:scale-110 transition-transform`}>
                                    <item.icon size={16} />
                                </div>
                                <div className="text-left">
                                    <span className="text-xs font-bold text-gray-800 block">
                                        {item.title}
                                    </span>
                                    <span className="text-[9px] text-gray-400 font-medium">
                                        {item.desc}
                                    </span>
                                </div>
                            </div>
                            <div className="px-6">
                                <ChevronRight size={14}
                                    className="text-gray-300 group-hover:text-[#7E57FF] group-hover:translate-x-1 transition-all"
                                />
                            </div>
                        </button>
                    ))}
                </motion.div>
                <div className="px-6">
                    {isLoggedIn ? (
                        <motion.button
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.3 }}
                            onClick={handleLogout}
                            className="w-full mt-6 py-3 text-red-400 font-bold text-xs flex items-center justify-center gap-2 hover:bg-red-50 rounded-xl transition-all border border-red-100 active:scale-95"
                        >
                            <LogOut size={16} />退出当前账号
                        </motion.button>
                    ) : (
                        <motion.button
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.3 }}
                            onClick={() => navigate('/login')}
                            className="w-full mt-6 py-3 text-[#7E57FF] font-bold text-xs flex items-center justify-center gap-2 hover:bg-purple-50 rounded-xl transition-all border border-purple-100 active:scale-95"
                        >
                            去登录
                        </motion.button>
                    )}
                </div>
            </div>
        </div>
    );
};

export default Profile;
