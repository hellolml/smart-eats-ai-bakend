import Layout from "@/app/layout";
import React from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { AppConfigProvider, useAppConfig } from "@/app/app-config";
import ProtectedRoute from "@/components/ProtectedRoute";
import { routes } from "./nav-items";

const RouteGuard: React.FC<{ path: string; children: React.ReactNode }> = ({ path, children }) => {
  const { config, loading } = useAppConfig();

  if (loading) {
    return <div className="p-6 text-sm text-gray-500">加载中...</div>;
  }

  if (path === '/register' && !config.auth.register) {
    return <Navigate to="/login" replace />;
  }

  if (path === '/oauth/github/callback' && !config.auth.oauth.github) {
    return <Navigate to="/login" replace />;
  }

  return <ProtectedRoute>{children}</ProtectedRoute>;
};

const AppRoutes: React.FC = () => {
  return (
    <Layout>
      <Toaster />
      <Routes>
        {routes.map(({ to, page }) => (
          <Route key={to} path={to} element={<RouteGuard path={to}>{page}</RouteGuard>} />
        ))}
        <Route
          key="/"
          path="/"
          element={<RouteGuard path="/">{routes.find((item) => item.isDefault)?.page}</RouteGuard>} />
      </Routes>
    </Layout>
  );
};

const App: React.FC = () => {
  return (
    <HashRouter>
      <AppConfigProvider>
        <AppRoutes />
      </AppConfigProvider>
    </HashRouter>
  );
};

export default App;
