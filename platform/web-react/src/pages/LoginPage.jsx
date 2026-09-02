import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

// Role-based login: there is exactly one form here, phone + OTP, no "log in as"
// dropdown. The role that determines which interface renders next comes back from
// the backend on the verified JWT (see AuthContext + RoleGate) — a rider account can
// never see the driver console by picking a different login option, because there
// isn't one. This is what makes the interface genuinely role-driven rather than a
// UI-only illusion.
const ROLE_LANDING = { RIDER: "/rider", DRIVER: "/driver", ADMIN: "/admin" };

export default function LoginPage() {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState("phone"); // "phone" | "otp"
  const [devCode, setDevCode] = useState(null);
  const [busy, setBusy] = useState(false);
  const { requestOtp, verifyOtp } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();

  async function handleRequestOtp(e) {
    e.preventDefault();
    if (!phone.trim()) return toast("Enter a phone number");
    setBusy(true);
    try {
      const resp = await requestOtp(phone.trim());
      setStep("otp");
      if (resp.dev_code) {
        setDevCode(resp.dev_code);
        setCode(resp.dev_code);
      }
    } catch (e) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleVerify(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const resp = await verifyOtp(phone.trim(), code.trim());
      navigate(ROLE_LANDING[resp.role] || "/", { replace: true });
    } catch (e) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-box">
        <h1>🚕 RideOps</h1>
        <div className="sub">Sign in — your account's role decides your interface</div>

        {step === "phone" ? (
          <form onSubmit={handleRequestOtp}>
            <label>Phone number</label>
            <input
              type="tel" placeholder="+919000000001" value={phone}
              onChange={(e) => setPhone(e.target.value)} autoFocus
            />
            <button className="btn primary" disabled={busy}>Send OTP</button>
          </form>
        ) : (
          <form onSubmit={handleVerify}>
            <label>OTP code</label>
            <input
              type="text" placeholder="6-digit code" maxLength={6} value={code}
              onChange={(e) => setCode(e.target.value)} autoFocus
            />
            <button className="btn primary" disabled={busy}>Verify &amp; sign in</button>
            <button type="button" className="btn ghost" onClick={() => setStep("phone")}>Back</button>
            {devCode && (
              <div className="dev-code-hint">
                Dev mode — your code is <b>{devCode}</b> (no real SMS is sent tonight)
              </div>
            )}
          </form>
        )}

        <div className="muted seed-hint">
          Seeded accounts — Rider: +919000000001..05 · Driver: +918000000001..12 · Admin: +910000000001
        </div>
      </div>
    </div>
  );
}
