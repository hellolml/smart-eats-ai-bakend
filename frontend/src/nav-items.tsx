import React from 'react';
import Index from '@/pages/Index';
import HomeChef from '@/pages/HomeChef';
import FoodHunter from '@/pages/FoodHunter';
import Profile from '@/pages/Profile';
import Preferences from '@/pages/Preferences';
import SecuritySettings from '@/pages/SecuritySettings';
import BlindBox from '@/pages/BlindBox';
import Wheel from '@/pages/Wheel';
import AiChat from '@/pages/AiChat';
import Login from '@/pages/Login';
import Register from '@/pages/Register';
import SessionManagement from '@/pages/SessionManagement';
import SkillManagement from '@/pages/SkillManagement';
import OAuthGithubCallback from '@/pages/OAuthGithubCallback';
import GroupDecisionResult from '@/pages/GroupDecisionResult';
import GroupDecisionCreate from '@/pages/GroupDecisionCreate';
import router from '@/config/router.json';
import { AUTH_NAV_PATHS, GUEST_NAV_PATHS } from '@/config/access-control';
import type { AppAuthPublicConfig } from '@/services/app-api';

import {
    Utensils,
    MapPin,
    User,
    Sparkles,
    Settings2,
    Package,
    RotateCw,
    LogIn,
    UserPlus,
    Shield,
    MessageSquare,
    Compass,
    Puzzle
} from 'lucide-react';

const routeMap: Record<string, React.ReactNode> = {
    Index : <Index />,
    HomeChef: <HomeChef />,
    FoodHunter: <FoodHunter />,
    Profile: <Profile />,
    Preferences: <Preferences />,
    SecuritySettings: <SecuritySettings />,
    BlindBox: <BlindBox />,
    Wheel: <Wheel />,
    AiChat: <AiChat />,
    TravelPlanner: (
        <AiChat
            scene="travel_planner"
            title="旅行规划助手"
            assistantName="行程管家"
            newSessionTitle="新旅行计划"
            placeholder="说说目的地、天数、同行人和想玩的内容..."
            emptyText="想去哪里走走？"
            starterPrompts={[
                '帮我规划杭州3天2晚，偏自然风景和本地小吃',
                '把这批旅行链接整理成可执行行程',
                '周末上海出发，做一个轻松亲子游路线'
            ]}
        />
    ),
    Login: <Login />,
    Register: <Register />,
    SessionManagement: <SessionManagement />,
    SkillManagement: <SkillManagement />,
    OAuthGithubCallback: <OAuthGithubCallback />,
    GroupDecisionResult: <GroupDecisionResult />,
    GroupDecisionCreate: <GroupDecisionCreate />
};

const routerIconMap: Record<string, any> = {
    Index: Sparkles,
    HomeChef: Utensils,
    FoodHunter: MapPin,
    Profile: User,
    Preferences: Settings2,
    SecuritySettings: Shield,
    BlindBox: Package,
    Wheel: RotateCw,
    AiChat: MessageSquare,
    TravelPlanner: Compass,
    Login: LogIn,
    Register: UserPlus,
    SessionManagement: Shield,
    SkillManagement: Puzzle
};

const allRoutes = Object.entries(router).map(([key, value]) => ({
    title: value.title,
    to: value.path,
        page: routeMap[key],
        isDefault: value.isDefault || false,
        icon: routerIconMap[key] || Sparkles,
        
}));

/* 底部导航栏显示的项 */
export function getNavItems(isLoggedIn: boolean, config?: AppAuthPublicConfig) {
    const allowed = isLoggedIn ? AUTH_NAV_PATHS : GUEST_NAV_PATHS;
    return allRoutes.filter((item) => {
        if (!(allowed as readonly string[]).includes(item.to)) {
            return false;
        }
        if (item.to === '/register' && config && !config.auth.register) {
            return false;
        }
        if (item.to === '/oauth/github/callback' && config && !config.auth.oauth.github) {
            return false;
        }
        return true;
    });
}

export const routes = allRoutes;
