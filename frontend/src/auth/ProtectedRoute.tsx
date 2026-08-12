import { Navigate, Outlet, useLocation } from "react-router-dom";

import { LoadingState } from "../components/ui/LoadingState";
import { useAuth } from "./AuthProvider";

export function ProtectedRoute() {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "checking") {
    return <LoadingState label="Checking your session" />;
  }

  if (auth.status !== "authenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
