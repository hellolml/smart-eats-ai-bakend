import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import toast from 'react-hot-toast';
import { authStore } from '@/services/app-api';
import { isProtectedRoute } from '@/config/access-control';

interface ProtectedRouteProps {
    children: React.ReactNode;
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
    const location = useLocation();
    const loggedIn = authStore.isLoggedIn();
    const protectedRoute = isProtectedRoute(location.pathname);

    if (!loggedIn && protectedRoute) {
        toast.error('登录后可使用更多功能', { id: 'auth-required' });
        return <Navigate to="/login" replace state={{ from: location.pathname }} />;
    }

    return <>{children}</>;
};

export default ProtectedRoute;

