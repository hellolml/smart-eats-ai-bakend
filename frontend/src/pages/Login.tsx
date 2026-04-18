import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion } from 'framer-motion';
import { Mail, Phone, Lock, ArrowRight, Sparkles, AlertCircle, MessageSquareText, KeyRound, Github } from "lucide-react";
import toast from "react-hot-toast";
import { ApiError, appApi } from "@/services/app-api";
import { useAppConfig } from "@/app/app-config";

const Login = () => {
    const navigate = useNavigate();
    const { config } = useAppConfig();
    const [loginType, setLoginType] = useState<'phone' | 'email'>('phone');
    const [loginMode, setLoginMode] = useState<'password' | 'otp'>('password');
    const [loading, setLoading] = useState(false);
    const [identifier, setIdentifier] = useState('');
    const [password, setPassword] = useState('');
    const [otpCode, setOtpCode] = useState('');
    const [oneClickToken, setOneClickToken] = useState('');
    const [showPasswordRule, setShowPasswordRule] = useState(false);

    const channelOptions = useMemo(() => {
        const items: Array<'phone' | 'email'> = [];
        if (config.auth.phone_enabled) items.push('phone');
        if (config.auth.email_enabled) items.push('email');
        return items;
    }, [config.auth.email_enabled, config.auth.phone_enabled]);

    useEffect(() => {
        if (!channelOptions.includes(loginType)) {
            setLoginType(channelOptions[0] || 'phone');
        }
    }, [channelOptions, loginType]);

    useEffect(() => {
        if (loginMode === 'otp' && !config.auth.otp_login) {
            setLoginMode('password');
        }
    }, [config.auth.otp_login, loginMode]);

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

    const handleOtpRequest = async () => {
        if (!identifier.trim()) {
            toast.error('请先填写手机号或邮箱');
            return;
        }
        toast.loading('正在发送验证码...', { id: 'login-otp-request' });
        try {
            const data = await appApi.auth.loginOtpRequest({ account: identifier.trim() });
            if (data.debug_code) {
                setOtpCode(data.debug_code);
            }
            toast.success('验证码已发送', { id: 'login-otp-request' });
        } catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || '发送验证码失败', { id: 'login-otp-request' });
                return;
            }
            toast.error('发送验证码失败，请稍后重试', { id: 'login-otp-request' });
        }
    };

    const handleOtpLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!identifier.trim() || !otpCode.trim()) {
            toast.error('请填写完整登录信息');
            return;
        }
        setLoading(true);
        toast.loading('正在登录...', { id: 'login' });
        try {
            await appApi.auth.loginOtpConfirm({ account: identifier.trim(), code: otpCode.trim() });
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

    const handleGithubLogin = async () => {
        toast.loading('正在跳转 GitHub...', { id: 'github-login' });
        try {
            localStorage.removeItem('app_oauth_action');
            const data = await appApi.auth.oauthStart('github');
            window.location.href = data.auth_url;
        } catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || 'GitHub 登录暂不可用', { id: 'github-login' });
                return;
            }
            toast.error('GitHub 登录暂不可用', { id: 'github-login' });
        }
    };

    const handleOneClickLogin = async () => {
        if (!oneClickToken.trim()) {
            toast.error('请先填写一键登录 token');
            return;
        }
        toast.loading('正在登录...', { id: 'one-click-login' });
        try {
            await appApi.auth.loginOneClick({ token: oneClickToken.trim() });
            toast.success('欢迎回来！', { id: 'one-click-login' });
            navigate('/');
        } catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || '一键登录失败', { id: 'one-click-login' });
                return;
            }
            toast.error('一键登录失败，请稍后重试', { id: 'one-click-login' });
        }
    };

    const showChannelTabs = channelOptions.length > 1;
    const showLoginModeTabs = config.auth.otp_login;

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
                    {showLoginModeTabs && (
                        <div className="flex p-1 bg-gray-50 rounded-xl md:rounded-2xl">
                            <button
                                type="button"
                                onClick={() => setLoginMode('password')}
                                className={`flex-1 py-2 md:py-2.5 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginMode === 'password' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                            >
                                密码登录
                            </button>
                            <button
                                type="button"
                                onClick={() => setLoginMode('otp')}
                                className={`flex-1 py-2 md:py-2.5 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginMode === 'otp' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                            >
                                验证码登录
                            </button>
                        </div>
                    )}

                    {showChannelTabs && (
                        <div className="flex p-1 bg-gray-50 rounded-xl md:rounded-2xl">
                            {config.auth.phone_enabled && (
                                <button
                                    type="button"
                                    onClick={() => setLoginType('phone')}
                                    className={`flex-1 py-2 md:py-2.5 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginType === 'phone' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                                >
                                    手机号
                                </button>
                            )}
                            {config.auth.email_enabled && (
                                <button
                                    type="button"
                                    onClick={() => setLoginType('email')}
                                    className={`flex-1 py-2 md:py-2.5 text-[11px] md:text-sm font-bold rounded-lg md:rounded-xl transition-all ${loginType === 'email' ? 'bg-white text-[#7E57FF] shadow-sm' : 'text-gray-400'}`}
                                >
                                    邮箱
                                </button>
                            )}
                        </div>
                    )}

                    <form onSubmit={loginMode === 'password' ? handlePasswordLogin : handleOtpLogin} className="flex flex-col gap-3 md:gap-4">
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
                            {loginMode === 'password' ? (
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
                                        <AlertCircle size={16} />
                                    </button>
                                    {showPasswordRule && (
                                        <div className="absolute z-20 right-0 top-[110%] w-56 rounded-xl border bg-white p-2 text-[11px] text-gray-600 shadow">
                                            密码需满足：8-64位，且至少包含1个字母和1个数字。
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="grid grid-cols-[1fr_auto] gap-2">
                                    <div className="relative">
                                        <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-gray-400">
                                            <MessageSquareText size={16} />
                                        </div>
                                        <input
                                            type="text"
                                            placeholder="请输入验证码"
                                            required
                                            value={otpCode}
                                            onChange={(e) => setOtpCode(e.target.value)}
                                            className="w-full bg-gray-50 border-none rounded-xl md:rounded-2xl py-3 px-4 pl-11 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                        />
                                    </div>
                                    <button
                                        type="button"
                                        onClick={handleOtpRequest}
                                        className="px-4 rounded-xl bg-purple-50 text-[#7E57FF] text-xs font-bold whitespace-nowrap"
                                    >
                                        发送验证码
                                    </button>
                                </div>
                            )}
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

                    {config.auth.one_click && (
                        <div className="space-y-2 rounded-2xl bg-gray-50 p-3">
                            <div className="flex items-center gap-2 text-xs font-bold text-gray-600">
                                <KeyRound size={14} />
                                一键登录
                            </div>
                            <div className="flex gap-2">
                                <input
                                    type="text"
                                    value={oneClickToken}
                                    onChange={(e) => setOneClickToken(e.target.value)}
                                    placeholder="请输入一键登录 token"
                                    className="flex-1 bg-white border-none rounded-xl py-3 px-4 text-xs md:text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                />
                                <button
                                    type="button"
                                    onClick={handleOneClickLogin}
                                    className="px-4 rounded-xl bg-[#7E57FF] text-white text-xs font-bold whitespace-nowrap"
                                >
                                    登录
                                </button>
                            </div>
                        </div>
                    )}

                    {config.auth.oauth.github && (
                        <button
                            type="button"
                            onClick={handleGithubLogin}
                            className="w-full border border-gray-200 bg-white text-gray-700 py-3 rounded-xl md:rounded-2xl font-bold flex items-center justify-center gap-2 hover:bg-gray-50 transition-all text-xs md:text-sm"
                        >
                            <Github size={16} />
                            使用 GitHub 登录
                        </button>
                    )}

                    {config.auth.register && (
                        <div className="text-center">
                            <p className="text-[10px] md:text-xs text-gray-400">
                                还没有账号? {' '}
                                <Link to="/register" className="text-[#7E57FF] font-bold hover:underline">
                                    立即注册
                                </Link>
                            </p>
                        </div>
                    )}
                </div>

            </motion.div>
        </div>
    );
};

export default Login;
