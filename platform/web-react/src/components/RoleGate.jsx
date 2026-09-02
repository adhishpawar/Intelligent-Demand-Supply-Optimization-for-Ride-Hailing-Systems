import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

// Client-side route guard — a UI convenience only. The REAL enforcement is the
// three-layer RBAC on the backend (gateway route table + independent per-service
// re-verification + row ownership, see PLAN §3.1); this component just keeps a
// rider from LANDING on a driver-shaped screen by URL guessing, it is not itself a
// security boundary — every API call this page makes is re-checked server-side
// regardless of what this component decided to render.
export default function RoleGate({ role, children }) {
  const { isAuthenticated, role: actualRole } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  if (actualRole !== role) return <Navigate to="/" replace />;
  return children;
}
