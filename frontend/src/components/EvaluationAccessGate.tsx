import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { appApi, ApiError } from '@/services/app-api';

interface EvaluationAccessGateProps {
    children: React.ReactNode;
}

const EvaluationAccessGate: React.FC<EvaluationAccessGateProps> = ({ children }) => {
    const [status, setStatus] = useState<'loading' | 'allowed' | 'forbidden' | 'unauthorized'>('loading');

    useEffect(() => {
        let cancelled = false;
        appApi.evaluations
            .checkAccess()
            .then((data) => {
                if (!cancelled) {
                    setStatus(data.allowed ? 'allowed' : 'forbidden');
                }
            })
            .catch((error: unknown) => {
                if (!cancelled) {
                    if (error instanceof ApiError && error.status === 401) {
                        setStatus('unauthorized');
                    } else if (error instanceof ApiError && error.status === 403) {
                        setStatus('forbidden');
                    } else {
                        setStatus('forbidden');
                    }
                }
            });
        return () => {
            cancelled = true;
        };
    }, []);

    if (status === 'loading') {
        return (
            <div className="flex items-center justify-center h-full min-h-[300px]">
                <div className="text-center">
                    <div className="w-8 h-8 border-2 border-gray-300 border-t-gray-800 rounded-full animate-spin mx-auto mb-3" />
                    <p className="text-sm text-gray-500">正在验证评测权限...</p>
                </div>
            </div>
        );
    }

    if (status === 'unauthorized') {
        return <Navigate to="/login" replace />;
    }

    if (status === 'forbidden') {
        return (
            <div className="flex items-center justify-center h-full min-h-[300px]">
                <div className="text-center max-w-sm mx-auto">
                    <div className="w-16 h-16 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-4">
                        <svg className="w-8 h-8 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                    </div>
                    <h2 className="text-lg font-semibold text-gray-900 mb-2">无访问权限</h2>
                    <p className="text-sm text-gray-500">你没有评测工作台的访问权限。请联系管理员将你添加到白名单。</p>
                </div>
            </div>
        );
    }

    return <>{children}</>;
};

export default EvaluationAccessGate;
