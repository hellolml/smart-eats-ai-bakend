import React from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { ApiError, appApi } from '@/services/app-api';
import { useAppConfig } from '@/app/app-config';

const OAuthGithubCallback: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { config } = useAppConfig();

  React.useEffect(() => {
    const code = searchParams.get('code') || '';
    const state = searchParams.get('state') || '';
    const action = localStorage.getItem('app_oauth_action') || 'login';

    if (!config.auth.oauth.github) {
      toast.error('GitHub 登录已关闭');
      navigate('/login', { replace: true });
      return;
    }

    if (!code || !state) {
      toast.error('GitHub 回调参数缺失');
      navigate('/login', { replace: true });
      return;
    }

    if (action === 'bind') {
      appApi.auth.oauthBind('github', { code, state })
        .then(() => {
          toast.success('GitHub 绑定成功');
          localStorage.removeItem('app_oauth_action');
          navigate('/security-settings', { replace: true });
        })
        .catch((error) => {
          toast.error(error instanceof ApiError ? error.message : 'GitHub 绑定失败');
          localStorage.removeItem('app_oauth_action');
          navigate('/security-settings', { replace: true });
        });
      return;
    }

    appApi.auth.oauthCallback('github', { code, state })
      .then(() => {
        toast.success('GitHub 登录成功');
        localStorage.removeItem('app_oauth_action');
        navigate('/', { replace: true });
      })
      .catch((error) => {
        toast.error(error instanceof ApiError ? error.message : 'GitHub 登录失败');
        localStorage.removeItem('app_oauth_action');
        navigate('/login', { replace: true });
      });
  }, [config.auth.oauth.github, navigate, searchParams]);

  return <div className="p-6 text-sm text-gray-500">GitHub 登录处理中...</div>;
};

export default OAuthGithubCallback;
