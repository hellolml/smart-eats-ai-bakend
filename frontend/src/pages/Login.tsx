import React, { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion } from 'framer-motion';
import { Mail, Phone, Lock, ArrowRight, Github, Chrome, Sparkles } from "lucide-react";
import toast from "react-hot-toast";
import { ApiError, appApi } from "@/services/app-api";

const Login = () => {
    const navigate = useNavigate();
    const [loginType, setLoginType] = useState<'phone' | 'email'>('phone');
    const [loading, setLoading] = useState(false);
    const [identifier, setIdentifier] = useState('');
    const [password, setPassword] = useState('');

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!identifier.trim() || !password) {
            toast.error('请填写完整登录信息');
            return;
        }

        setLoading(true);
        toast.loading('正在登陆...', { id: 'login' });

        try {
            if (loginType === 'phone') {
                await appApi.auth.login({
                    phone: identifier.trim(),
                    password
                });
            } else {
                await appApi.auth.login({
                    email: identifier.trim(),
                    password
                });
            }
            setLoading(false);
            toast.success('欢迎回来！', { id: 'login' });
            navigate('/');
        } catch (error) {
            setLoading(false);
            if (error instanceof ApiError) {
                toast.error(error.message || '登录失败', { id: 'login' });
                return;
            }
            toast.error('登录失败，请稍后重试', { id: 'login' });
        }
    };

    return (
        <div className="h-full flex flex-col justify-center items-center px-4 py-4 overflow-hidden">
            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="w-full max-w-md flex flex-col gap-4 md:gap-6">

                {/**login && title */}
                <div className="text-center space-y-1">
                    <div className="inline-flex items-center justify-center w-12 h-12 md:w-16 md:h-16 bg-[#7E57FF] rounded-[1.5rem] rounded-[2rem] shadow-xl shadow-purple-100 mb-2">
                        <Sparkles className="text-white w-6 h-6 md:w-8 md:h-8" />
                    </div>
                    <h1 className="text-2xl md:text-3xl font-black text-gray-800 tracking-tight">吃点啥？</h1>
                    <p className="text-gray-400 text-[10px] md:text-xs font-medium">吃！</p>
                </div>

                {/**login form */}
                <div className="bg-white rounded-[2rem] rounded-[2.5rem] p-5 md:p-8 shadow-sm border border-purple-50 flex flex-col gap-4 md:gap-6">
                    <div className="flex p-1 bg-gray-50 rounded-xl md:rounded-2xl">
                        <button
                            onClick={() => setLoginType('phone')}
                            className={`flex-1 py-2 md:py-2.5 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginType === 'phone'
                                ? 'bg-white text-[#7E57FF] shadow-sm'
                                : 'text-gray-400'
                                }`} >
                            手机号登录
                        </button>
                        <button
                            onClick={() => setLoginType('email')}
                            className={`flex-1 py-2 md:py-2.5 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginType === 'email'
                                ? 'bg-white text-[#7E57FF] shadow-sm'
                                : 'text-gray-400'
                                }`} >
                            邮箱登录
                        </button>
                    </div>
                    <form onSubmit={handleLogin} className="flex flex-col gap-3 md:gap-4">
                        <div className="space-y-3">
                            <div className="relative">
                                <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                    {loginType === 'phone' ? (
                                        <Phone size={16} />
                                    ) : (
                                        <Mail size={16} />
                                    )}
                                </div>
                                <input
                                    type={loginType === 'phone' ? 'tel' : 'email'}
                                    placeholder={
                                        loginType === 'phone' ? '请输入手机号' : '请输入邮箱地址'}
                                    required
                                    value={identifier}
                                    onChange={(e) => setIdentifier(e.target.value)}
                                    className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all" />
                            </div>
                            <div className="relative">
                                <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                    <Lock size={18} />
                                </div>
                                <input
                                    type="password"
                                    placeholder="请输入密码"
                                    required
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all" />
                            </div>
                        </div>
                        <div className="flex justify-end">
                            <button
                                type="button"
                                className="text-[10px] md:text-xs font-bold text-[#7E57FF] hover:underline">
                                忘记密码？
                            </button>
                        </div>

                        <button
                            type="submit"
                            disabled={loading}
                            className="w-full bg-[#7E57FF] text-white py-3 md:py-4 rounded-xl md:rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center justify-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95 disabled:opacity-70 text-xs md:text-sm">

                            {loading ? '登录中...' : '立即登录'}
                            {!loading && <ArrowRight size={16} />}
                        </button>
                    </form>
                    <div className="text-center">
                        <p className="text-[10px] md:text-xs text-gray-400">
                            还没有账号? {' '}
                            <Link to="/register"
                                className="text-[#7E57FF] font-bold hover:underline">
                                立即注册
                            </Link>
                        </p>
                    </div>
                </div>

                {/**三方登录 */}
                <div className="space-y-3">
                    <div className="flex items-center gap-3">
                        <div className="flex-1 h-px bg-gray-100" />
                        <span className="text-[9px] md:text-[10px] text-gray-300 font-bold uppercase tracking-widest">
                            第三方登录
                        </span>
                        <div className="flex-1 h-px bg-gray-100" />
                    </div>
                    <div className="flex justify-center gap-3 md:gap-4">
                        <button className="w-10 h-10 md:w-12 md:h-12 bg-white rounded-xl md:rounded-2xl border border-gray-50 flex items-center justify-center text-gray-600 hover:bg-gray-50
transition-colors shadow-sm">
                            <Chrome size={20} />
                        </button>
                        <button className="w-10 h-10 md:w-12 md:h-12 bg-white rounded-xl md:rounded-2xl border border-gray-50 flex items-center justify-center text-gray-600 hover:bg-gray-50
transition-colors shadow-sm">
                            <Github size={20} />
                        </button>
                        <button className="w-10 h-10 md:w-12 md:h-12 bg-white rounded-xl md:rounded-2xl border border-gray-50 flex items-center justify-center text-[#00A1E9] hover:bg-gray-50
transition-colors shadow-sm font-bold text-lg md:text-xl">
                            支
                        </button>
                    </div>
                </div>
            </motion.div >
        </div >
    );
};

export default Login;
