import { useCallback, useEffect, useState } from "react";
import { api, OnboardingEnv, Status } from "../api";
import { attempt } from "../hooks";

type TargetPage = "home" | "voices" | "settings";

interface Props {
  status: Status;
  toast: (m: string, error?: boolean) => void;
  goto: (p: TargetPage) => void;
  onFinish: () => void;
  onClose: () => void;
}

const STEPS = [
  { id: 0, label: "1. 环境检查" },
  { id: 1, label: "2. 虚拟声卡" },
  { id: 2, label: "3. 功能教学" },
] as const;

export default function OnboardingModal({ status, toast, goto, onFinish, onClose }: Props) {
  const err = useCallback((m: string) => toast(m, true), [toast]);
  const [step, setStep] = useState(0);
  const [env, setEnv] = useState<OnboardingEnv | null>(null);
  const [checking, setChecking] = useState(false);
  const loadEnv = useCallback(
    async (refresh = false, silent = false) => {
      if (!silent) setChecking(true);
      const data = await attempt(() => api.onboardingEnv(refresh), err);
      if (!silent) setChecking(false);
      if (data) setEnv(data);
      return data ?? null;
    },
    [err],
  );

  const triggerEnsure = useCallback(async () => {
    const data = await attempt(api.ensureOnboardingEnv, err);
    if (data) setEnv(data);
  }, [err]);

  useEffect(() => {
    void loadEnv();
  }, [loadEnv]);

  // Poll while background environment installation is running.
  useEffect(() => {
    if (env?.installTask.status !== "running") return;
    const timer = window.setInterval(() => {
      loadEnv(false, true);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [env?.installTask.status, loadEnv]);

  // Close with Escape key.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const openVbCable = async () => {
    const res = await attempt(() => api.openOnboardingUrl("vb_cable"), err);
    if (res?.ok) toast("已在浏览器打开 VB-CABLE 下载页");
  };

  const recheckCable = async () => {
    const data = await loadEnv(!status.running);
    if (!data) return;
    if (data.vbCable.installed) {
      toast("已检测到虚拟声卡 VB-CABLE");
    } else {
      toast("暂未检测到 VB-CABLE，装完后建议重启电脑再试", true);
    }
  };

  const jumpTo = (p: TargetPage) => {
    onFinish();
    goto(p);
  };

  const envReady = Boolean(env && env.python.ok && env.uv.installed);
  const installing = env?.installTask.status === "running";
  const cableReady = Boolean(env?.vbCable.installed);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="新手引导">
      <div className="modal wizard">
        <div className="wizard-head">
          <div>
            <h2>欢迎使用声音面板 · 新手引导</h2>
            <p className="muted small" style={{ margin: "2px 0 0" }}>
              3 步完成环境自检、虚拟声卡安装与上手教学
            </p>
          </div>
          <button className="ghost small-btn" onClick={onClose} aria-label="关闭引导">
            ✕
          </button>
        </div>

        <div className="wizard-steps">
          {STEPS.map((s) => (
            <button
              key={s.id}
              type="button"
              className={"wizard-tab" + (step === s.id ? " active" : "")}
              onClick={() => setStep(s.id)}
            >
              <span>{s.label}</span>
              {s.id === 0 && env && (
                <span className={"step-badge " + (envReady ? "ok" : installing ? "accent-text" : "warn-text")}>
                  {envReady ? "✓" : installing ? "…" : "!"}
                </span>
              )}
              {s.id === 1 && env && (
                <span className={"step-badge " + (cableReady ? "ok" : "warn-text")}>{cableReady ? "✓" : "!"}</span>
              )}
            </button>
          ))}
        </div>

        <div className="wizard-body">
          {step === 0 && (
            <div className="wizard-section">
              <div className="row" style={{ marginBottom: 8 }}>
                <h3 style={{ margin: 0 }}>自动检查 Python 与 uv 环境</h3>
                <button
                  className="ghost small-btn"
                  onClick={() => loadEnv(false)}
                  disabled={checking || installing}
                >
                  {checking ? "检查中…" : "↻ 重新检查"}
                </button>
              </div>
              <p className="muted small" style={{ marginTop: 0 }}>
                引导会检查 Python 3.12 与 uv。缺少组件时，可点击按钮下载安装。
              </p>

              {!env ? (
                <div className="callout">正在检测本机环境…</div>
              ) : (
                <div className="check-list">
                  <div className={"check-item" + (env.python.ok ? " done" : " warn")}>
                    <span className="check-dot">{env.python.ok ? "✓" : "!"}</span>
                    <div className="grow">
                      <div className="check-title">Python {env.python.required}</div>
                      <div className="muted small">
                        {env.python.ok
                          ? `已就绪：Python ${env.python.version}`
                          : `当前为 Python ${env.python.version}，需要 ${env.python.required}`}
                      </div>
                    </div>
                  </div>

                  <div className={"check-item" + (env.uv.installed ? " done" : " warn")}>
                    <span className="check-dot">{env.uv.installed ? "✓" : "!"}</span>
                    <div className="grow">
                      <div className="check-title">uv 依赖管理器</div>
                      <div className="muted small">
                        {env.uv.installed
                          ? `已就绪：${env.uv.version ?? "uv"}`
                          : installing
                            ? "未检测到 uv，正在自动安装…"
                            : "未检测到 uv"}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {env && env.installTask.status !== "idle" && (
                <div
                  className={
                    "callout" +
                    (env.installTask.status === "error" ? " warn-callout" : "")
                  }
                >
                  <div className="row">
                    <b>
                      {env.installTask.status === "running"
                        ? "⏳ 正在自动安装缺失组件…"
                        : env.installTask.status === "success"
                          ? "✓ 自动检查与安装已完成"
                          : "⚠ 自动安装遇到问题"}
                    </b>
                    {env.installTask.status === "error" && (
                      <button className="small-btn" onClick={triggerEnsure}>
                        重试自动安装
                      </button>
                    )}
                  </div>
                  <div className="small" style={{ marginTop: 4 }}>
                    {env.installTask.message}
                  </div>
                  {env.installTask.logs.length > 0 && (
                    <pre className="log-box">{env.installTask.logs.slice(-8).join("\n")}</pre>
                  )}
                </div>
              )}

              {env && !envReady && !installing && env.installTask.status !== "error" && (
                <div className="row" style={{ marginTop: 12, justifyContent: "flex-start" }}>
                  <button onClick={triggerEnsure}>立即自动安装缺失项</button>
                </div>
              )}
            </div>
          )}

          {step === 1 && (
            <div className="wizard-section">
              <div className="row" style={{ marginBottom: 8 }}>
                <h3 style={{ margin: 0 }}>安装虚拟声卡（VB-CABLE）</h3>
                <button className="ghost small-btn" onClick={recheckCable} disabled={checking}>
                  {checking ? "检测中…" : "↻ 重新检测"}
                </button>
              </div>
              <p className="muted small" style={{ marginTop: 0 }}>
                虚拟声卡用来把浏览器的网课声音送进变声器，再把变声后的声音输出到你的耳机或音箱。
              </p>

              <div className={"check-item" + (cableReady ? " done" : " warn")}>
                <span className="check-dot">{cableReady ? "✓" : "!"}</span>
                <div className="grow">
                  <div className="check-title">
                    {cableReady ? "已检测到 VB-CABLE 虚拟声卡" : "尚未检测到 VB-CABLE 虚拟声卡"}
                  </div>
                  <div className="muted small">
                    {cableReady
                      ? `当前输入设备：${env?.vbCable.deviceName}`
                      : "请点击下方按钮前往官网免费下载并安装"}
                  </div>
                </div>
              </div>

              <div className="callout" style={{ marginTop: 12 }}>
                <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
                  <button onClick={openVbCable}>打开 VB-CABLE 官网下载 ↗</button>
                  <span className="muted small mono-sm">{env?.vbCable.downloadUrl ?? "https://vb-audio.com/Cable/"}</span>
                </div>
                <ol className="guide-ol">
                  <li>在官网下载 VB-CABLE 压缩包并完整解压到本地文件夹。</li>
                  <li>
                    右键以<b>管理员身份</b>运行 <code>VBCABLE_Setup_x64.exe</code> 并点击 Install Driver。
                  </li>
                  <li>安装完成后建议重启 Windows，再回来点「重新检测」。</li>
                </ol>
              </div>

              {cableReady && (
                <div className="callout">
                  <b>下一步接线提示：</b>在 Windows 声音设置里，把正在放网课的浏览器输出（或系统默认输出）设为{" "}
                  <b>CABLE Input</b>。
                  <button className="link" onClick={() => attempt(() => api.openSoundSettings("apps-volume"), err)}>
                    打开 Windows 应用音量设置
                  </button>
                </div>
              )}
            </div>
          )}

          {step === 2 && (
            <div className="wizard-section">
              <h3 style={{ marginTop: 0 }}>3 步快速上手</h3>
              <div className="guide-list">
                <div className="guide-card">
                  <span className="step-num">1</span>
                  <div className="grow">
                    <div className="check-title">
                      导入并启用声音模型
                      {env && env.voiceCount > 0 && (
                        <span className="tag soft">已有 {env.voiceCount} 个声音</span>
                      )}
                    </div>
                    <p className="muted small" style={{ margin: "4px 0 8px" }}>
                      进入「声音库」选择本地 RVC 模型（<code>.pth</code>，可附带 <code>.index</code>），导入后点「使用」。
                    </p>
                    <button className="ghost small-btn" onClick={() => jumpTo("voices")}>
                      去声音库
                    </button>
                  </div>
                </div>

                <div className="guide-card">
                  <span className="step-num">2</span>
                  <div className="grow">
                    <div className="check-title">把浏览器声音接到虚拟声卡</div>
                    <p className="muted small" style={{ margin: "4px 0 8px" }}>
                      在 Windows 音量合成器里把浏览器输出设为 <b>CABLE Input</b>；面板「设置」里输入保持{" "}
                      <b>CABLE Output</b>、输出保持<b>跟随系统默认输出</b>。
                    </p>
                    <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
                      <button
                        className="ghost small-btn"
                        onClick={() => attempt(() => api.openSoundSettings("apps-volume"), err)}
                      >
                        打开 Windows 声音设置
                      </button>
                      <button className="ghost small-btn" onClick={() => jumpTo("settings")}>
                        去设备设置
                      </button>
                    </div>
                  </div>
                </div>

                <div className="guide-card">
                  <span className="step-num">3</span>
                  <div className="grow">
                    <div className="check-title">开始变声与日常微调</div>
                    <ul className="guide-ul small muted">
                      <li>
                        回「主页」点<b>开始变声</b>，播放网课即可实时听到效果。
                      </li>
                      <li>
                        <b>自动匹配音高</b>：自动根据老师音高升降调；觉得太尖把目标 Hz 调低，太闷调高。
                      </li>
                      <li>
                        <b>临时听原声</b>：不停止变声，随时一键切回老师原本声音。
                      </li>
                      <li>
                        <b>偶发卡顿/爆音</b>：去「设置」把延迟档位切到<b>稳定</b>。
                      </li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="wizard-foot">
          <button className="ghost" onClick={onFinish}>
            不再自动弹出
          </button>
          <div className="row" style={{ gap: 8 }}>
            {step > 0 && (
              <button className="ghost" onClick={() => setStep((s) => s - 1)}>
                上一步
              </button>
            )}
            {step < STEPS.length - 1 ? (
              <button onClick={() => setStep((s) => s + 1)}>下一步</button>
            ) : (
              <button onClick={onFinish}>完成并开始使用</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
