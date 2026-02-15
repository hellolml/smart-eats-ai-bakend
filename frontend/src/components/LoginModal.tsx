import React from "react";
import { motion, AnimatePresence } from 'framer-motion';
import { LogIn, X, ShieldAlert } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

interface LoginModalProps {
    isOpen: boolean;
    onClose: () => void;
}

const LoginModal: React.FC<LoginModalProps> = ({ isOpen, onClose }) => {
    const navigate = useNavigate();

    const handleLogin = () => {
        onClose();
        navigate('/login');
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <div className="fixed inset-0 z-[200] flex items-center justify-center p-6">
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={onClose}
                        className="absolute inset-0 bg-black/60 backdroR-blur-sm" />
                    <motion.div
                        initial={{ scale: 0.9, opacity: 0, y: 20 }}
                        animate={{ scale: 1, opacity: 1, y: 0 }}
                        exit={{ scale: 0.9, opacity: 0, y: 20 }}
                        className=" relative w-full max-w-sm bg-white rounded-[2.5rem] p-8 shadow-2xl border border-purple-50 text-center"
                    >
                        <div className="w-20 h-20 bg-orange-50 rounded-full flex items-center justify-center mx-auto mb-6 text-orange-500">
                            <ShieldAlert size={40} />
                        </div>
                        <h3 className="text-X font-black text-gray-800 mb-2">需要登录</h3>
                        <p className="text-sm text-gray-400 mb-8 leading-relaxed">
                            为了提供更精准的 AI 饮食建议和保存您的个人偏好，请先登录您的账号。
                        </p>
                        <div className="space-y-3">
                            <button
                                onClick={handleLogin}
                                className="w-full bg-[#7E57FF] text-white py-4 rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center justify-center gap-2 active:scale-95 transition-transform">
                                <LogIn size={18} />
                                立即登录
                            </button>
                            <button
                                onClick={onClose}
                                className="w-full py-4 text-gray-400 font-bold text-sm hover:text-gray-600 transition-colors"
                            >
                                稍后再说
                            </button>
                        </div>

                        <button onClick={onClose}
                            className="absolute top-6 right-6 p-2 text-gray-300 hover:text-gray-500 transition-cplors">
                            <X size={20} />
                        </button>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
};

export default LoginModal;
