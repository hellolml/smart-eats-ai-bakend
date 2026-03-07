import React from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { ApiError, appApi } from '@/services/app-api';

const OAuthGithubCallback: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  React.useEffect(() => {
    const run = async () => {
      const code = searchParams.get('code') || '';
      const state = searchParams.get('state') || '';
      if (!code || !state) {
        toast.error('GitHub 回调参数缺失');
        navigate('/login', { replace: true });
        return;
      }

      toast.loading('正在完成 GitHub 登录...', { id: 'oauth-callback' });
      try {
        const data = await appApi.auth.oauthCallback('github', { code, state });
        const isNewUser = Boolean(data?.oauth && (data.oauth as any).is_new_user);
        toast.success(isNewUser ? 'GitHub 注册并登录成功' : 'GitHub 登录成功', { id: 'oauth-callback' });
        navigate('/', { replace: true });
      } catch (error) {
        const message = error instanceof ApiError ? error.message : 'GitHub 登录失败';
        toast.error(message, { id: 'oauth-callback' });
        navigate('/login', { replace: true });
      }
    };
    run();
  }, [navigate, searchParams]);

  return <div className="p-6 text-sm text-gray-500">正在处理 GitHub 登录，请稍候...</div>;
};

export default OAuthGithubCallback;
