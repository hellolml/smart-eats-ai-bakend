import React from 'react';
import { appApi, type AppAuthPublicConfig } from '@/services/app-api';

const DEFAULT_CONFIG: AppAuthPublicConfig = {
    ready: true,
    auth: {
        password_login: true,
        register: true,
        otp_login: false,
        otp_register: false,
        password_reset: false,
        one_click: false,
        oauth: { github: false },
        phone_enabled: true,
        email_enabled: true,
    },
    checks: {},
};

const AppConfigContext = React.createContext<{
    config: AppAuthPublicConfig;
    loading: boolean;
    refresh: () => Promise<void>;
}>({
    config: DEFAULT_CONFIG,
    loading: true,
    refresh: async () => {},
});

export function AppConfigProvider({ children }: { children: React.ReactNode }) {
    const [config, setConfig] = React.useState<AppAuthPublicConfig>(DEFAULT_CONFIG);
    const [loading, setLoading] = React.useState(true);

    const refresh = React.useCallback(async () => {
        setLoading(true);
        try {
            const next = await appApi.auth.publicConfig({ force: true });
            setConfig(next);
        } catch {
            setConfig(DEFAULT_CONFIG);
        } finally {
            setLoading(false);
        }
    }, []);

    React.useEffect(() => {
        let mounted = true;
        appApi.auth
            .publicConfig()
            .then((next) => {
                if (mounted) {
                    setConfig(next);
                }
            })
            .catch(() => {
                if (mounted) {
                    setConfig(DEFAULT_CONFIG);
                }
            })
            .finally(() => {
                if (mounted) {
                    setLoading(false);
                }
            });
        return () => {
            mounted = false;
        };
    }, []);

    const value = React.useMemo(() => ({ config, loading, refresh }), [config, loading, refresh]);

    return <AppConfigContext.Provider value={value}>{children}</AppConfigContext.Provider>;
}

export function useAppConfig() {
    return React.useContext(AppConfigContext);
}
