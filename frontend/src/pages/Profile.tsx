import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, LogOut, Pencil, Check, Camera, Clock, Settings2 } from 'lucide-react';
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
            toast.success('昵称已更新');
        } catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || '更新失败');
                return;
            }
            toast.error('更新失败');
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
        } catch (error) {
            toast.error('更新失败');
        }
    };

    return (
        <div className="h-full flex flex-col overflow-hidden pb-20 md:pb-4 animate-in fade-in duration-500 relative">
            <div className="absolute top-0 left-0 right-0 h-24 bg-gradient-to-b from-[#7E57FF]/10 to-transparent -z-10" />

            <motion.section
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex-shrink-0 pt-4 pb-6 px-6 flex items-center gap-4 bg-white shadow-sm"
            >
                <div className="relative flex-shrink-0">
                    <div className="w-16 h-16 rounded-[1.5rem] bg-gradient-to-tr from-[#7E57FF] to-purple-300 p-0.5 shadow-lg shadow-purple-100">
                        <div className="w-full h-full rounded-[1.4rem] bg-white flex items-center justify-center overflow-hidden">
                            <img
                                src={profile?.avatar || ''}
                                alt="Avatar"
                                className="w-full h-full object-cover"
                            />
                        </div>
                    </div>

                    <button
                        onClick={handleChangeAvatar}
                        className="absolute -bottom-1 -right-1 w-6 h-6 bg-white rounded-lg shadow-md flex items-center justify-center text-[#7E57FF] border border-purple-50 active:scale-90 transition-transform"
                    >
                        <Camera size={12} />
                    </button>
                </div>

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
                                <button onClick={handleUpdateName} className="text-green-500 p-1 bg-green-50 rounded-lg shadow-sm flex-shrink-0">
                                    <Check size={14} />
                                </button>
                            </motion.div>
                        ) : (
                            <motion.div
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 1 }}
                                className="flex items-center gap-2"
                            >
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
                        <span className="text-[9px] text-gray-400 font-medium">ID: {profile?.id || '88888888'}</span>
                        <div className="flex items-center gap-1 text-[9px] text-gray-400 font-medium">
                            <Clock size={9} className="text-purple-300" />
                            <span>已加入{profile?.joined_days ?? 1}天</span>
                        </div>
                    </div>
                </div>
            </motion.section>

            <div className="flex-1 px-6 pt-6">
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="rounded-[2rem] border border-purple-50 bg-white p-5 shadow-sm"
                >
                    <h3 className="text-sm font-black text-gray-800">个人信息</h3>
                    <p className="mt-1 text-xs text-gray-400">这里展示你的账号资料和使用状态，应用设置请前往“设置”模块。</p>
                </motion.div>

                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.15 }}
                    className="mt-4 bg-white border border-purple-50 rounded-[1.5rem] shadow-sm overflow-hidden"
                >
                    <button
                        onClick={() => navigate('/preferences')}
                        className="w-full px-0 py-3 flex items-center justify-between hover:bg-gray-50 transition-all group active:bg-purple-50/30"
                    >
                        <div className="flex items-center gap-3 px-5">
                            <div className="bg-orange-50 text-orange-500 p-2 rounded-lg group-hover:scale-110 transition-transform">
                                <Settings2 size={16} />
                            </div>
                            <div className="text-left">
                                <span className="text-xs font-bold text-gray-800 block">偏好设置</span>
                                <span className="text-[9px] text-gray-400 font-medium">口味、忌口与饮食习惯</span>
                            </div>
                        </div>
                        <div className="px-5">
                            <ChevronRight
                                size={14}
                                className="text-gray-300 group-hover:text-[#7E57FF] group-hover:translate-x-1 transition-all"
                            />
                        </div>
                    </button>
                </motion.div>

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
    );
};

export default Profile;
