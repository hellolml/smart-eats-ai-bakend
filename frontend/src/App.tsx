import Layout from "@/app/layout";
import React from "react";
import { HashRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { routes } from "./nav-items";

const App: React.FC = () => {
  return (
    <HashRouter>
      <Layout>
        <Toaster />
        <Routes>
          {routes.map(({ to, page }) => (
            <Route key={to} path={to} element={page} />
          ))}
          <Route
            key="/"
            path="/"
            element={routes.find((item) => item.isDefault)?.page} />
        </Routes>
      </Layout>
    </HashRouter>
  );
};

export default App;