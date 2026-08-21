import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthProvider";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./components/layout/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { InboxPage } from "./pages/InboxPage";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { WorkforcePage } from "./pages/WorkforcePage";
import { WorkItemsPage } from "./pages/WorkItemsPage";
import { WorkspaceProvider } from "./workspaces/WorkspaceProvider";
import { AppExperienceProvider } from "./app/AppExperience";
import { PublicLayout } from "./components/public/PublicLayout";
import { PublicPage } from "./pages/PublicPage";
import { RegisterPage } from "./pages/RegisterPage";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => {
          const status = error instanceof Error && "status" in error ? error.status : undefined;
          if (status === 401 || status === 403 || status === 404) {
            return false;
          }
          return failureCount < 1;
        },
        refetchOnWindowFocus: false
      }
    }
  });
}

export function App() {
  const [queryClient] = useState(makeQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <AppExperienceProvider>
        <AuthProvider>
          <WorkspaceProvider>
          <Routes>
            <Route element={<PublicLayout />}>
              <Route path="/" element={<LandingPage />} />
              <Route path="/platform" element={<PublicPage page="platform" />} />
              <Route path="/how-it-works" element={<PublicPage page="how" />} />
              <Route path="/sales" element={<PublicPage page="sales" />} />
              <Route path="/about" element={<PublicPage page="about" />} />
              <Route path="/contact" element={<PublicPage page="contact" />} />
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<RegisterPage />} />
            </Route>
            <Route element={<ProtectedRoute />}>
              <Route path="/app" element={<AppShell />}>
                <Route index element={<DashboardPage />} />
                <Route path="inbox" element={<InboxPage />} />
                <Route path="workforce" element={<WorkforcePage />} />
                <Route path="work-items" element={<WorkItemsPage />} />
                <Route path="approvals" element={<ApprovalsPage />} />
                <Route path="leads" element={<PlaceholderPage title="Leads" />} />
                <Route path="products" element={<PlaceholderPage title="Products" />} />
                <Route path="integrations" element={<PlaceholderPage title="Integrations" />} />
                <Route path="analytics" element={<AnalyticsPage />} />
                <Route path="settings" element={<PlaceholderPage title="Settings" />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </WorkspaceProvider>
        </AuthProvider>
      </AppExperienceProvider>
    </QueryClientProvider>
  );
}
