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
import GroupDecisionResult from '@/pages/GroupDecisionResult';
import router from '@/config/router.json';
import { AUTH_NAV_PATHS, GUEST_NAV_PATHS } from '@/config/access-control';

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
    MessageSquare
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
    Login: <Login />,
    Register: <Register />,
    GroupDecisionResult: <GroupDecisionResult />
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
    Login: LogIn,
    Register: UserPlus
};

const allRoutes = Object.entries(router).map(([key, value]) => ({
    title: value.title,
    to: value.path,
        page: routeMap[key],
        isDefault: value.isDefault || false,
        icon: routerIconMap[key] || Sparkles,
        
}));

/* 底部导航栏显示的项 */
export function getNavItems(isLoggedIn: boolean) {
    const allowed = isLoggedIn ? AUTH_NAV_PATHS : GUEST_NAV_PATHS;
    return allRoutes.filter((item) => (allowed as readonly string[]).includes(item.to));
}

export const routes = allRoutes;
