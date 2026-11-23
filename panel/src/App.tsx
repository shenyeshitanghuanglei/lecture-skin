import { useState } from "react";
import { useStatus, useToast } from "./hooks";
import Home from "./pages/Home";
import Voices from "./pages/Voices";
import Settings from "./pages/Settings";
import Clone from "./pages/Clone";
import OnboardingModal from "./components/OnboardingModal";

type Page = "home" | "voices" | "clone" | "settings";

const ONBOARDING_DONE_KEY = "vt_onboarding_done_v1";

function isOnboardingDone(): boolean {
  try {
    return window.localStorage.getItem(ONBOARDING_DONE_KEY) === "1";
  } catch {
    return false;
  }
}

const NAV: { id: Page; label: string; icon: string }[] = [
  { id: "home", label: "主页", icon: "◉" },
  { id: "voices", label: "声音库", icon: "♪" },
  { id: "clone", label: "声音克隆", icon: "✦" },
  { id: "settings", label: "设置", icon: "⚙" },
];

export default function App() {
  const [page, setPage] = useState<Page>("home");
  const { status, setStatus, offline, refresh } = useStatus();
  const activePage = page === "clone" && !status?.cloneEnabled ? "home" : page;
  const toast = useToast();
  const [onboardingDone, setOnboardingDone] = useState<boolean>(isOnboardingDone);
  const [showOnboarding, setShowOnboarding] = useState<boolean>(() => !isOnboardingDone());

  const finishOnboarding = () => {
    try {
      window.localStorage.setItem(ONBOARDING_DONE_KEY, "1");
    } catch {
      /* ignore storage errors */
    }
    setOnboardingDone(true);
    setShowOnboarding(false);
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-dot" data-on={status?.running && !status.passThrough ? "1" : "0"} />
          声音面板
        </div>
        <nav>
          {NAV.filter((n) => n.id !== "clone" || status?.cloneEnabled).map((n) => (
            <button
              key={n.id}
              className={"nav-item" + (activePage === n.id ? " active" : "")}
              onClick={() => setPage(n.id)}
            >
              <span className="nav-icon">{n.icon}</span>
              {n.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <button className="nav-item sidebar-guide" onClick={() => setShowOnboarding(true)}>
            <span className="nav-icon">?</span>
            新手引导
            {!onboardingDone && <span className="guide-dot" aria-hidden="true" />}
          </button>
          <div className="sidebar-status">
            {offline ? <span className="bad">● 正在重连服务…</span> : <span className="ok">● 服务已连接</span>}
          </div>
        </div>
      </aside>

      <main className="content">
        {offline && !status && (
          <div className="card center">
            <h2>正在连接变声服务…</h2>
            <p className="muted">服务启动需要 10 秒左右。如果一直连不上，请关闭窗口重新打开。</p>
          </div>
        )}
        {offline && status && (
          <div className="card warn small" style={{ marginBottom: 14 }}>
            与变声服务的连接暂时中断，正在自动重连…
          </div>
        )}
        {status && activePage === "home" && (
          <Home status={status} setStatus={setStatus} refreshStatus={refresh} toast={toast.show} goto={setPage} />
        )}
        {status && activePage === "voices" && <Voices status={status} refreshStatus={refresh} toast={toast.show} />}
        {status && activePage === "clone" && (
          <Clone status={status} refreshStatus={refresh} toast={toast.show} gotoVoices={() => setPage("voices")} />
        )}
        {status && activePage === "settings" && <Settings status={status} setStatus={setStatus} toast={toast.show} />}
      </main>

      {status && showOnboarding && (
        <OnboardingModal
          status={status}
          toast={toast.show}
          goto={setPage}
          onFinish={finishOnboarding}
          onClose={() => setShowOnboarding(false)}
        />
      )}

      {toast.msg && <div className={"toast" + (toast.msg.error ? " error" : "")}>{toast.msg.text}</div>}
    </div>
  );
}
