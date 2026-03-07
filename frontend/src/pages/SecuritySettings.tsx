import React,{ useState } from "react";
import { useNavigate } from 'react-router-dom';
import {
    ChevronLeft, Save, Lock, ShieldCheck,
    Eye, EyeOff, Smartphone
} from 'lucide-react';
import toast from "react-hot-toast";
import { ApiError, appApi } from "@/services/app-api";

const SecuritySettings = () => {
    const navigate = useNavigate();
    const [oldPassword, setOldPassword] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [showPass, setShowPass] = useState(false);
    const handleSave = async () => {
        if (!oldPassword || !newPassword || !confirmPassword) {
            toast.error('请填写完整信息');
            return;
        }
        if (newPassword != confirmPassword) {
            toast.error('两次输入的新密码不一致');
            return;
        }
        if (newPassword.length < 6) {
            toast.error('新密码长度不能少于6位');
            return;
        }

        toast.loading('正在更新密码...', { id: 'update-pass' });

        try {
            await appApi.auth.changePassword({
                oldPassword,
                newPassword
            });
            toast.success('密码已成功修改', { id: 'update-pass' });
            navigate('/profile');
        } catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || '密码修改失败', { id: 'update-pass' });
                return;
            }
            toast.error('密码修改失败，请稍后重试', { id: 'update-pass' });
        }
    };

    return (
        <div className="space-y-6 pb-10 no-scrollbar">
            <div className="flex items-center gap-4">
                <button
                    onClick={() => navigate('/profile')}
                    className="p-2 bg-white rounded-xl shadow-sm text-gray-600 active:scale-90 transition-transform"
                >
                    <ChevronLeft size={20} />
                </button>
                <h2 className="text-xl font-bold text-gray-800">安全设置</h2>
            </div>
            <div className="bg-purple-50 p-6 rounded-[2.5rem] flex items-center gap-4 border border-purple-100">
                <div className="w-12 h-12 bg-white rounded-2xl flex items-center justify-center text-[#7E57FF] shadow-sm">
                    <ShieldCheck size={24} />
                </div>
                <div>
                    <h3 className="text-sm font-bold text-gray-800">账号安全保护</h3>
                    <p className="text-[10px] text-purple-400 mt-1">
                        定期更换密码可以有效保护您的账号安全
                    </p>
                </div>
            </div>

            <section className="bg-white rounded-[2.5rem] p-8 shadow-sm border border-purple-50 space-y-6">
                <div className="space-y-1.5">
                    <label className="text-[9px] font-bold text-gray-400 uppercase ml-1">
                        当前密码
                    </label>
                    <div className="relative">
                        <div className="absolute inset-y-0 left-3.5 flex items-center text-gray-400">
                            <Lock size={14} />
                        </div>
                        <input
                            type={showPass ? 'text' : 'password'}
                            value={oldPassword}
                            onChange={(e) => setOldPassword(e.target.value)}
                            placeholder="请输入当前密码"
                            className="w-full bg-gray-50 border-none rounded-2xl py-3 pl-10 pr-10 text-sm outline-none focus:ring-1 focus:ring-purple-200"
                        />
                        <button
                            onClick={() => setShowPass(!showPass)}
                            className="absolute inset-y-0 right-3.5 flex items-center text-gray-400">
                            {showPass ? <EyeOff size={16} /> : <Eye size={16} />}
                        </button>
                    </div>
                </div>
                <div className="space-y-1.5">
                    < label className="text-[9px] font-bold text-gray-400 uppercase ml-1">
                        新密码
                    </label>
                    <div className="relative">
                        <div className="absolute inset-y-0 left-3.5 flex items-center text-gray-400">
                            <Lock size={14} />
                        </div>
                        <input
                            type="password"
                            value={newPassword}
                            onChange={(e) => setNewPassword(e.target.value)}
                            placeholder="请输入新密码"
                            className="w-full bg-gray-50 border-none rounded-2xl py-3 pl-10 pr-10 text-sm outline-none focus:ring-1 focus:ring-purple-200"
                        />
                    </div>
                </div>
                <div className="space-y-1.5">
                    < label className="text-[9px] font-bold text-gray-400 uppercase ml-1">
                        确认新密码
                    </label>
                    <div className="relative">
                        <div className="absolute inset-y-0 left-3.5 flex items-center text-gray-400">
                            <Lock size={14} />
                        </div>
                        <input
                            type="password"
                            value={confirmPassword}
                            onChange={(e) => setConfirmPassword(e.target.value)}
                            placeholder="请再次输入新密码"
                            className="w-full bg-gray-50 border-none rounded-2xl py-3 pl-10 pr-10 text-sm outline-none focus:ring-1 focus:ring-purple-200"
                        />
                    </div>
                </div>
            </section>

            <button
                onClick={() => navigate('/security/sessions')}
                className="w-full bg-white text-gray-700 py-3.5 rounded-2xl border flex items-center justify-center gap-2"
            >
                <Smartphone size={18} />
                会话管理（设备下线）
            </button>
            <button
                onClick={handleSave}
                className="w-full bg-[#7E57FF] text-white py-3.5 rounded-2xl shadow-lg shadow-purple-200 flex items-center justify-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95 flex-shrink-0"
            >
                <Save size={18} />
                确认修改密码
            </button>
        </div>
    );
};

export default SecuritySettings;
