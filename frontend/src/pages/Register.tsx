import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
    User, Mail, Lock, ArrowRight, ChevronLeft,
    ShieldCheck, Phone, Sparkles, MessageSquare, CircleQuestionMark
} from 'lucide-react';
import toast from 'react-hot-toast';
import { ApiError, appApi } from '@/services/app-api';

const Register = () => {
    const navigate = useNavigate();
    const [loading, setLoading] = useState(false);
    const [registerType, setRegisterType] = useState<'phone' | 'email'>('phone');
    const [name, setName] = useState('');
    const [identifier, setIdentifier] = useState('');
    const [password, setPassword] = useState('');
    const [code, setCode] = useState('');
    const [showPasswordRule, setShowPasswordRule] = useState(false);

    const requestOtp = async () => {
        if (!identifier.trim()) {
            toast.error(registerType === 'phone' ? '请输入手机号' : '请输入邮箱');
            return;
        }
        setLoading(true);
        toast.loading('正在发送验证码...', { id: 'register-otp' });
        try {
            const payload = registerType === 'phone'
                ? { phone: identifier.trim() }
                : { email: identifier.trim() };
            const data = await appApi.auth.registerRequestOtp(payload);
            if (data.debug_code) {
                setCode(data.debug_code);
                toast.success(`验证码已发送（开发模式：${data.debug_code}）`, { id: 'register-otp' });
            } else {
                toast.success('验证码已发送，请查收', { id: 'register-otp' });
            }
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : '验证码发送失败', { id: 'register-otp' });
        } finally {
            setLoading(false);
        }
    };

    const handleRegister = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!name.trim() || !identifier.trim() || !password || !code.trim()) {
            toast.error('请完整填写注册信息与验证码');
            return;
        }

        setLoading(true);
        toast.loading('正在完成注册...', { id: 'register' });

        try {
            if (registerType === 'phone') {
                await appApi.auth.registerConfirm({
                    name: name.trim(),
                    phone: identifier.trim(),
                    password,
                    code: code.trim(),
                });
            } else {
                await appApi.auth.registerConfirm({
                    name: name.trim(),
                    email: identifier.trim(),
                    password,
                    code: code.trim(),
                });
            }

            toast.success('注册成功，已自动登录', { id: 'register' });
            navigate('/');
        } catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || '注册失败', { id: 'register' });
                return;
            }
            toast.error('注册失败，请稍后重试', { id: 'register' });
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="h-full flex flex-col justify-center items-center px-4 py-4 overflow-hidden">
            <motion.div
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                className="w-full max-w-md flex flex-col gap-3 md:gap-5">
                <button
                    onClick={() => navigate('/login')}
                    className="inline-flex items-center gap-1.5 text-gray-400 hover:text-[#7E57FF] transition-colors w-fit"
                >
                    <ChevronLeft size={20} />
                    <span className="text-[11px] md:text-sm font-bold">返回登录</span>
                </button>

                <div className="space-y-1">
                    <div className="flex items-center gap-2">
                        <Sparkles className="text-[#7E57FF] w-5 h-5"/>
                        <h1 className="text-2xl md:text-3xl font-black text-gray-800 tracking-tight">
                            吃点啥？
                        </h1>
                    </div>
                    <p className="text-gray-400 text-[10px] md:text-xs font-medium">
                        加入我们
                    </p>
                </div>

                <div className="bg-white rounded-[2rem] md:rounded-[2.5rem] p-5 md:p-8 shadow-sm border border-purple-50 flex flex-col gap-4 md:gap-6">
                    <div className="flex p-1 bg-gray-50 rounded-xl md:rounded-2xl">
                        <button
                            onClick={() => setRegisterType('phone')}
                            className={`flex-1 py-2 md:py-2.5 text-[11px] text-sm font-bold rounded-lg md:rounded-xl transition-all ${registerType === 'phone'
                                ? 'bg-white text-[#7E57FF] shadow-sm'
                                : 'text-gray-400'}`}
                        >
                            手机号注册
                        </button>
                        <button
                            onClick={() => setRegisterType('email')}
                            className={`flex-1 py-2 md:py-2.5 text-[11px] text-sm font-bold rounded-lg md:rounded-xl transition-all ${registerType === 'email'
                                ? 'bg-white text-[#7E57FF] shadow-sm'
                                : 'text-gray-400'}`}
                        >
                            邮箱注册
                        </button>
                    </div>

                    <form onSubmit={handleRegister} className="flex flex-col gap-3 md:gap-4">
                        <div className="space-y-3">
                            <div className="relative">
                                <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                    <User size={16} />
                                </div>
                                <input
                                    type="text"
                                    placeholder="您的昵称"
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all" />
                            </div>
                            <div className='relative'>
                                <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                    {registerType === 'phone' ? (
                                        <Phone size={16} />) : (
                                        <Mail size={16} />
                                    )}
                                </div>
                                <input
                                    type={registerType === 'phone' ? 'tel' : 'email'}
                                    placeholder={registerType === 'phone' ? '请输入手机号' : '请输入邮箱地址'}
                                    required
                                    value={identifier}
                                    onChange={(e) => setIdentifier(e.target.value)}
                                    className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all" />
                            </div>

                            <div className='flex gap-2'>
                                <div className="relative flex-1">
                                    <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                        <MessageSquare size={16} />
                                    </div>
                                    <input
                                        type="text"
                                        placeholder="请输入验证码"
                                        value={code}
                                        onChange={(e) => setCode(e.target.value)}
                                        className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all" />
                                </div>
                                <button
                                    type="button"
                                    onClick={requestOtp}
                                    disabled={loading}
                                    className="px-3 py-2 text-xs rounded-xl bg-gray-100 text-gray-700 disabled:opacity-60"
                                >
                                    发验证码
                                </button>
                            </div>

                            <div className="relative">
                                <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                    <Lock size={18} />
                                </div>
                                <input
                                    type="password"
                                    placeholder="设置密码"
                                    required
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 pr-10 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all" />
                                <button
                                    type="button"
                                    onClick={() => setShowPasswordRule((v) => !v)}
                                    className="absolute inset-y-0 right-3 flex items-center text-gray-400"
                                    aria-label="密码规则"
                                >
                                    <CircleQuestionMark size={16} />
                                </button>
                                {showPasswordRule && (
                                    <div className="absolute z-20 right-0 top-[110%] w-56 rounded-xl border bg-white p-2 text-[11px] text-gray-600 shadow">
                                        密码需满足：8-64位，且至少包含1个字母和1个数字。
                                    </div>
                                )}
                            </div>
                        </div>
                        <div className="flex items-start gap-2 px-1">
                            <div className='mt-0.5'>
                                <ShieldCheck size={12} className="text-[#7E57FF]" />
                            </div>
                            <p className="text-[10px] md:text-[10px] text-gray-400 leading-tight">
                                注册即代表您同意我们的{'  '}
                                <span className="text-[#7E57FF] font-bold">服务协议</span>和{'  '}
                                <span className="text-[#7E57FF] font-bold">隐私政策</span>
                            </p>
                        </div>
                        <button
                            type='submit'
                            disabled={loading}
                            className="w-full bg-[#7E57FF] text-white py-3 md:py-4 rounded-xl md:rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center justify-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95 disabled:opacity-70 text-xs md:text-sm">
                            {loading ? '注册中...' : '立即注册'}
                            {!loading && <ArrowRight size={18} />}
                        </button>
                    </form>
                    <div className="text-center">
                        <p className="text-[10px] md:text-xs text-gray-400">
                            已经有账号了?{'  '}
                            <Link to="/login" className="text-[#7E57FF] font-bold hover:underline">
                                去登录
                            </Link>
                        </p>
                    </div>
                </div>
            </motion.div>
        </div>
    );
};

export default Register;
