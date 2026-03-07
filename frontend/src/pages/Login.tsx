import React, { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion } from 'framer-motion';
import { Mail, Phone, Lock, ArrowRight, Github, Chrome, Sparkles, MessageSquare, Zap, CircleHelp } from "lucide-react";
import toast from "react-hot-toast";
import { ApiError, appApi } from "@/services/app-api";

type LoginMode = 'password' | 'sms' | 'oneclick';

const Login = () => {
    const navigate = useNavigate();
    const showOneClickLogin = String(__APP_SHOW_ONECLICK_LOGIN__ || 'false').toLowerCase() === 'true';
    const [loginType, setLoginType] = useState<'phone' | 'email'>('phone');
    const [loginMode, setLoginMode] = useState<LoginMode>('password');
    const [loading, setLoading] = useState(false);

    const [identifier, setIdentifier] = useState('');
    const [password, setPassword] = useState('');

    const [otpType, setOtpType] = useState<'phone' | 'email'>('phone');
    const [otpAccount, setOtpAccount] = useState('');
    const [smsCode, setSmsCode] = useState('');

    const [oneClickToken, setOneClickToken] = useState('');

    const [showResetModal, setShowResetModal] = useState(false);
    const [resetAccount, setResetAccount] = useState('');
    const [resetCode, setResetCode] = useState('');
    const [resetNewPassword, setResetNewPassword] = useState('');

    const [showPasswordRule, setShowPasswordRule] = useState(false);
    const [showResetPasswordRule, setShowResetPasswordRule] = useState(false);

    const handlePasswordLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!identifier.trim() || !password) {
            toast.error('请填写完整登录信息');
            return;
        }

        setLoading(true);
        toast.loading('正在登录...', { id: 'login' });

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
            toast.success('欢迎回来！', { id: 'login' });
            navigate('/');
        } catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || '登录失败', { id: 'login' });
                return;
            }
            toast.error('登录失败，请稍后重试', { id: 'login' });
        } finally {
            setLoading(false);
        }
    };

    const handleSmsRequest = async () => {
        if (!otpAccount.trim()) {
            toast.error(otpType === 'phone' ? '请输入手机号' : '请输入邮箱');
            return;
        }
        setLoading(true);
        toast.loading('正在发送验证码...', { id: 'sms-request' });
        try {
            const data = await appApi.auth.loginOtpRequest({ account: otpAccount.trim() });
            if (data.debug_code) {
                setSmsCode(data.debug_code);
                toast.success(`验证码已发送（开发模式：${data.debug_code}）`, { id: 'sms-request' });
            } else {
                toast.success(otpType === 'phone' ? '验证码已发送，请查收短信' : '验证码已发送，请查收邮箱', { id: 'sms-request' });
            }
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : '验证码发送失败', { id: 'sms-request' });
        } finally {
            setLoading(false);
        }
    };

    const handleSmsConfirm = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!otpAccount.trim() || !smsCode.trim()) {
            toast.error(otpType === 'phone' ? '请填写手机号和验证码' : '请填写邮箱和验证码');
            return;
        }

        setLoading(true);
        toast.loading('正在验证并登录...', { id: 'sms-confirm' });
        try {
            await appApi.auth.loginOtpConfirm({
                account: otpAccount.trim(),
                code: smsCode.trim()
            });
            toast.success(otpType === 'phone' ? '手机号验证码登录成功' : '邮箱验证码登录成功', { id: 'sms-confirm' });
            navigate('/');
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : '验证码登录失败', { id: 'sms-confirm' });
        } finally {
            setLoading(false);
        }
    };

    const handleOneClickLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!oneClickToken.trim()) {
            toast.error('请填写一键登录 token');
            return;
        }

        setLoading(true);
        toast.loading('正在一键登录...', { id: 'one-click' });
        try {
            await appApi.auth.loginOneClick({ token: oneClickToken.trim() });
            toast.success('一键登录成功', { id: 'one-click' });
            navigate('/');
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : '一键登录失败', { id: 'one-click' });
        } finally {
            setLoading(false);
        }
    };

    const handleGithubLogin = async () => {
        try {
            const data = await appApi.auth.oauthStart('github');
            window.location.href = data.auth_url;
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : '获取 GitHub 授权链接失败');
        }
    };

    const handleResetRequest = async () => {
        if (!resetAccount.trim()) {
            toast.error('请输入手机号或邮箱');
            return;
        }
        setLoading(true);
        toast.loading('正在发送重置验证码...', { id: 'reset-request' });
        try {
            const data = await appApi.auth.resetPasswordRequest({ account: resetAccount.trim() });
            if (data.debug_code) {
                setResetCode(data.debug_code);
                toast.success(`验证码已发送（开发模式：${data.debug_code}）`, { id: 'reset-request' });
            } else {
                toast.success('验证码已发送，请查收', { id: 'reset-request' });
            }
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : '发送失败', { id: 'reset-request' });
        } finally {
            setLoading(false);
        }
    };

    const handleResetConfirm = async () => {
        if (!resetAccount.trim() || !resetCode.trim() || !resetNewPassword) {
            toast.error('请填写完整重置信息');
            return;
        }
        setLoading(true);
        toast.loading('正在重置密码...', { id: 'reset-confirm' });
        try {
            await appApi.auth.resetPasswordConfirm({
                account: resetAccount.trim(),
                code: resetCode.trim(),
                newPassword: resetNewPassword,
            });
            toast.success('密码重置成功，请使用新密码登录', { id: 'reset-confirm' });
            setShowResetModal(false);
            setResetCode('');
            setResetNewPassword('');
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : '重置失败', { id: 'reset-confirm' });
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="h-full flex flex-col justify-center items-center px-4 py-4 overflow-hidden">
            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="w-full max-w-md flex flex-col gap-4 md:gap-6">

                <div className="text-center space-y-1">
                    <div className="inline-flex items-center justify-center w-12 h-12 md:w-16 md:h-16 bg-[#7E57FF] rounded-[2rem] shadow-xl shadow-purple-100 mb-2">
                        <Sparkles className="text-white w-6 h-6 md:w-8 md:h-8" />
                    </div>
                    <h1 className="text-2xl md:text-3xl font-black text-gray-800 tracking-tight">吃点啥？</h1>
                    <p className="text-gray-400 text-[10px] md:text-xs font-medium">吃！</p>
                </div>

                <div className="bg-white rounded-[2.5rem] p-5 md:p-8 shadow-sm border border-purple-50 flex flex-col gap-4 md:gap-6">
                    <div className="flex p-1 bg-gray-50 rounded-xl md:rounded-2xl">
                        <button
                            onClick={() => setLoginMode('password')}
                            className={`flex-1 py-2 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginMode === 'password' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                        >
                            密码登录
                        </button>
                        <button
                            onClick={() => setLoginMode('sms')}
                            className={`flex-1 py-2 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginMode === 'sms' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                        >
                            验证码登录
                        </button>
                        {showOneClickLogin && (
                            <button
                                onClick={() => setLoginMode('oneclick')}
                                className={`flex-1 py-2 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginMode === 'oneclick' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                            >
                                一键登录
                            </button>
                        )}
                    </div>

                    {loginMode === 'password' && (
                        <>
                            <div className="flex p-1 bg-gray-50 rounded-xl md:rounded-2xl">
                                <button
                                    onClick={() => setLoginType('phone')}
                                    className={`flex-1 py-2 md:py-2.5 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginType === 'phone' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                                >
                                    手机号
                                </button>
                                <button
                                    onClick={() => setLoginType('email')}
                                    className={`flex-1 py-2 md:py-2.5 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginType === 'email' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                                >
                                    邮箱
                                </button>
                            </div>

                            <form onSubmit={handlePasswordLogin} className="flex flex-col gap-3 md:gap-4">
                                <div className="space-y-3">
                                    <div className="relative">
                                        <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                            {loginType === 'phone' ? <Phone size={16} /> : <Mail size={16} />}
                                        </div>
                                        <input
                                            type={loginType === 'phone' ? 'tel' : 'email'}
                                            placeholder={loginType === 'phone' ? '请输入手机号' : '请输入邮箱地址'}
                                            required
                                            value={identifier}
                                            onChange={(e) => setIdentifier(e.target.value)}
                                            className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                        />
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
                                            className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 pr-10 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                        />
                                        <button
                                            type="button"
                                            onClick={() => setShowPasswordRule((v) => !v)}
                                            className="absolute inset-y-0 right-3 flex items-center text-gray-400"
                                            aria-label="密码规则"
                                        >
                                            <CircleHelp size={16} />
                                        </button>
                                        {showPasswordRule && (
                                            <div className="absolute z-20 right-0 top-[110%] w-56 rounded-xl border bg-white p-2 text-[11px] text-gray-600 shadow">
                                                密码需满足：8-64位，且至少包含1个字母和1个数字。
                                            </div>
                                        )}
                                    </div>
                                </div>

                                <div className="flex justify-end">
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setResetAccount(identifier.trim());
                                            setShowResetModal(true);
                                        }}
                                        className="text-[10px] md:text-xs font-bold text-[#7E57FF] hover:underline"
                                    >
                                        忘记密码？
                                    </button>
                                </div>

                                <button
                                    type="submit"
                                    disabled={loading}
                                    className="w-full bg-[#7E57FF] text-white py-3 md:py-4 rounded-xl md:rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center justify-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95 disabled:opacity-70 text-xs md:text-sm"
                                >
                                    {loading ? '登录中...' : '立即登录'}
                                    {!loading && <ArrowRight size={16} />}
                                </button>
                            </form>
                        </>
                    )}

                    {loginMode === 'sms' && (
                        <form onSubmit={handleSmsConfirm} className="flex flex-col gap-3 md:gap-4">
                            <div className="flex p-1 bg-gray-50 rounded-xl md:rounded-2xl">
                                <button
                                    type="button"
                                    onClick={() => setOtpType('phone')}
                                    className={`flex-1 py-2 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${otpType === 'phone' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                                >
                                    手机号验证码
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setOtpType('email')}
                                    className={`flex-1 py-2 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${otpType === 'email' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                                >
                                    邮箱验证码
                                </button>
                            </div>

                            <div className="relative">
                                <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                    {otpType === 'phone' ? <Phone size={16} /> : <Mail size={16} />}
                                </div>
                                <input
                                    type={otpType === 'phone' ? 'tel' : 'email'}
                                    placeholder={otpType === 'phone' ? '请输入手机号' : '请输入邮箱'}
                                    value={otpAccount}
                                    onChange={(e) => setOtpAccount(e.target.value)}
                                    className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                />
                            </div>

                            <div className="flex gap-2">
                                <div className="relative flex-1">
                                    <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                        <MessageSquare size={16} />
                                    </div>
                                    <input
                                        type="text"
                                        placeholder="请输入验证码"
                                        value={smsCode}
                                        onChange={(e) => setSmsCode(e.target.value)}
                                        className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                    />
                                </div>
                                <button
                                    type="button"
                                    onClick={handleSmsRequest}
                                    disabled={loading}
                                    className="px-3 py-2 text-xs rounded-xl bg-gray-100 text-gray-700 disabled:opacity-60"
                                >
                                    发验证码
                                </button>
                            </div>

                            <button
                                type="submit"
                                disabled={loading}
                                className="w-full bg-[#7E57FF] text-white py-3 md:py-4 rounded-xl md:rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center justify-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95 disabled:opacity-70 text-xs md:text-sm"
                            >
                                {loading ? '登录中...' : '验证码登录'}
                                {!loading && <ArrowRight size={16} />}
                            </button>
                        </form>
                    )}

                    {showOneClickLogin && loginMode === 'oneclick' && (
                        <form onSubmit={handleOneClickLogin} className="flex flex-col gap-3 md:gap-4">
                            <div className="relative">
                                <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                    <Zap size={16} />
                                </div>
                                <input
                                    type="text"
                                    placeholder="请输入一键登录 token（开发可用 mock:手机号）"
                                    value={oneClickToken}
                                    onChange={(e) => setOneClickToken(e.target.value)}
                                    className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                />
                            </div>

                            <button
                                type="submit"
                                disabled={loading}
                                className="w-full bg-[#7E57FF] text-white py-3 md:py-4 rounded-xl md:rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center justify-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95 disabled:opacity-70 text-xs md:text-sm"
                            >
                                {loading ? '登录中...' : '手机号一键登录'}
                                {!loading && <ArrowRight size={16} />}
                            </button>
                        </form>
                    )}

                    <div className="text-center">
                        <p className="text-[10px] md:text-xs text-gray-400">
                            还没有账号? {' '}
                            <Link to="/register" className="text-[#7E57FF] font-bold hover:underline">
                                立即注册
                            </Link>
                        </p>
                    </div>
                </div>

                <div className="space-y-3">
                    <div className="flex items-center gap-3">
                        <div className="flex-1 h-px bg-gray-100" />
                        <span className="text-[9px] md:text-[10px] text-gray-300 font-bold uppercase tracking-widest">
                            第三方登录
                        </span>
                        <div className="flex-1 h-px bg-gray-100" />
                    </div>
                    <div className="flex justify-center gap-3 md:gap-4">
                        <button className="w-10 h-10 md:w-12 md:h-12 bg-white rounded-xl md:rounded-2xl border border-gray-50 flex items-center justify-center text-gray-600 hover:bg-gray-50 transition-colors shadow-sm">
                            <Chrome size={20} />
                        </button>
                        <button
                            onClick={handleGithubLogin}
                            className="w-10 h-10 md:w-12 md:h-12 bg-white rounded-xl md:rounded-2xl border border-gray-50 flex items-center justify-center text-gray-600 hover:bg-gray-50 transition-colors shadow-sm"
                        >
                            <Github size={20} />
                        </button>
                        <button className="w-10 h-10 md:w-12 md:h-12 bg-white rounded-xl md:rounded-2xl border border-gray-50 flex items-center justify-center text-[#00A1E9] hover:bg-gray-50 transition-colors shadow-sm font-bold text-lg md:text-xl">
                            支
                        </button>
                    </div>
                </div>
            </motion.div>

            {showResetModal && (
                <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 px-4">
                    <div className="w-full max-w-sm bg-white rounded-2xl p-4 space-y-3">
                        <div className="text-sm font-semibold">重置密码</div>
                        <input
                            type="text"
                            placeholder="手机号或邮箱"
                            value={resetAccount}
                            onChange={(e) => setResetAccount(e.target.value)}
                            className="w-full bg-gray-50 rounded-xl px-3 py-2 text-sm outline-none"
                        />
                        <div className="flex gap-2">
                            <input
                                type="text"
                                placeholder="验证码"
                                value={resetCode}
                                onChange={(e) => setResetCode(e.target.value)}
                                className="flex-1 bg-gray-50 rounded-xl px-3 py-2 text-sm outline-none"
                            />
                            <button
                                onClick={handleResetRequest}
                                className="px-3 py-2 text-xs rounded-xl bg-gray-100 text-gray-700"
                            >
                                发验证码
                            </button>
                        </div>
                        <div className="relative">
                            <input
                                type="password"
                                placeholder="新密码"
                                value={resetNewPassword}
                                onChange={(e) => setResetNewPassword(e.target.value)}
                                className="w-full bg-gray-50 rounded-xl px-3 py-2 pr-9 text-sm outline-none"
                            />
                            <button
                                type="button"
                                onClick={() => setShowResetPasswordRule((v) => !v)}
                                className="absolute inset-y-0 right-2 flex items-center text-gray-400"
                                aria-label="密码规则"
                            >
                                <CircleHelp size={15} />
                            </button>
                            {showResetPasswordRule && (
                                <div className="absolute z-20 right-0 top-[110%] w-56 rounded-xl border bg-white p-2 text-[11px] text-gray-600 shadow">
                                    密码需满足：8-64位，且至少包含1个字母和1个数字。
                                </div>
                            )}
                        </div>
                        <div className="flex justify-end gap-2">
                            <button
                                onClick={() => setShowResetModal(false)}
                                className="px-3 py-2 text-xs rounded-xl bg-gray-100 text-gray-700"
                            >
                                取消
                            </button>
                            <button
                                onClick={handleResetConfirm}
                                className="px-3 py-2 text-xs rounded-xl bg-[#7E57FF] text-white"
                            >
                                确认重置
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default Login;
