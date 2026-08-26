import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import LoginPage from "./pages/LoginPage";
import RiderPage from "./pages/RiderPage";
import DriverPage from "./pages/DriverPage";
import AdminPage from "./pages/AdminPage";
import RoleGate from "./components/RoleGate";
import "./styles/app.css";

const ROLE_LANDING = { RIDER: "/rider", DRIVER: "/driver", ADMIN: "/admin" };

function RootRedirect() {
  const { isAuthenticated, role } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <Navigate to={ROLE_LANDING[role] || "/login"} replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/rider/*" element={<RoleGate role="RIDER"><RiderPage /></RoleGate>} />
            <Route path="/driver/*" element={<RoleGate role="DRIVER"><DriverPage /></RoleGate>} />
            <Route path="/admin/*" element={<RoleGate role="ADMIN"><AdminPage /></RoleGate>} />
            <Route path="/" element={<RootRedirect />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  );
}
