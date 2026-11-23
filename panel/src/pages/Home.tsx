import { useEffect, useRef, useState } from "react";
import { api, Status, VoiceList } from "../api";
import { attempt } from "../hooks";
import { SmoothSlider, VolumeSlider } from "./Settings";

interface Props {
  status: Status;
  setStatus: (s: Status) => void;
  refreshStatus: () => Promise<Status | null>;
  toast: (m: string, error?: boolean) => void;
  goto: (p: "voices" | "settings") => void;
}

const DB_MIN = -60;

function Meter({ db }: { db: number | null }) {
  const pct = db === null ? 0 : Math.max(0, Math.min(1, (db - DB_MIN) / -DB_MIN)) * 100;
  return (
    <div className="meter" aria-label="输入音量">
      <div className="meter-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function Home({ status, setStatus, refreshStatus, toast, goto }: Props) {
  const err = (m: string) => toast(m, true);
  const [voices, setVoices] = useState<VoiceList | null>(null);
  const [busy, setBusy] = useState(false);
  const [switching, setSwitching] = useState(false);
  const silentSince = useRef<number | null>(null);
  const [silentWarn, setSilentWarn] = useState(false);

  useEffect(() => {
    attempt(api.voices, err).then((v) => v && setVoices(v));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.voice?.slot]);

  // Warn when running but nothing arrives on the virtual cable for a while.
  useEffect(() => {
    const quiet = status.running && (status.audio.inDb === null || status.audio.inDb < -80);
    if (!quiet) {
      silentSince.current = null;
      setSilentWarn(false);
    } else if (silentSince.current === null) {
      silentSince.current = Date.now();
    } else if (Date.now() - silentSince.current > 8000) {
      setSilentWarn(true);
    }
  }, [status]);

  const toggle = async () => {
    setBusy(true);
    const s = await attempt(status.running ? api.stop : api.start, err);
    if (s) setStatus(s);
    setBusy(false);
  };

  const set = async (values: Record<string, unknown>) => {
    const s = await attempt(() => api.setSettings(values), err);
    if (s) setStatus(s);
  };

  // Apply now and remember on the current voice, so switching back restores it.
  const saveTarget = async (hz: number) => {
    await set({ autoPitchTargetHz: hz });
    if (status.voice) await attempt(() => api.patchVoice(status.voice!.slot, { targetPitchHz: hz }), err);
  };

  const selectVoice = async (slot: number) => {
    setSwitching(true);
    const v = await attempt(() => api.selectVoice(slot), err);
    if (v) {
      setVoices(v);
      await refreshStatus();
      toast("已切换声音");
    }
    setSwitching(false);
  };

  const p = status.perf;
  const on = status.running && !status.passThrough;
  const fmtTran = (t: number) => `${t > 0 ? "+" : ""}${t} 半音`;
  const tranText = fmtTran(status.pitch.tran);

  return (
    <div className="page">
      <section className={"card hero" + (on ? " on" : "")}>
        <div>
          <div className="hero-title">
            {status.running ? (status.passThrough ? "原声播放中" : "变声中") : status.originalAudio ? "已停止（原声直通）" : "已停止"}
          </div>
          <div className="muted">
            {status.voice ? `当前声音：${status.voice.name}` : "还没有选择声音"}
          </div>
        </div>
        <button className={"big-toggle" + (status.running ? " stop" : "")} onClick={toggle} disabled={busy || switching}>
          {busy ? "处理中…" : status.running ? "停止" : "开始变声"}
        </button>
      </section>

      {status.running && !status.system.defaultIsCable && (
        <section className="card warn">
          <b>浏览器的声音可能没有进入变声器。</b> 推荐把 Windows 的<b>默认输出</b>设为「CABLE Input」：
          浏览器会自动跟随，不用单独设置；变声后的声音仍从你的扬声器/耳机播放，系统音量键也照常可调。
          <div className="row" style={{ justifyContent: "flex-start", marginTop: 8 }}>
            <button onClick={() => attempt(() => api.openSoundSettings("sound"), err)}>打开声音设置</button>
          </div>
          <small className="muted">当前默认输出：{status.system.defaultOutput ?? "未知"}</small>
        </section>
      )}

      {status.warnings.length > 0 && (
        <section className="card warn">
          {status.warnings.map((w) => (
            <div key={w}>{w}</div>
          ))}
          <button className="link" onClick={() => goto("settings")}>
            去设置里选择设备
          </button>
        </section>
      )}

      {silentWarn && (
        <section className="card warn">
          <b>没有收到声音。</b> 请确认浏览器正在播放，并且它的输出设备是 <b>CABLE Input</b>。
          <button className="link" onClick={() => attempt(api.openSoundSettings, err)}>
            打开 Windows 应用音量设置
          </button>
        </section>
      )}

      <div className="grid">
        <section className="card">
          <div className="card-head">
            <h3>声音</h3>
            <span className="muted small">变声开始前1分钟左右出现声音不稳定属于正常现象</span>
          </div>
          {voices && voices.voices.length > 0 ? (
            <select value={voices.current} disabled={switching} onChange={(e) => selectVoice(Number(e.target.value))}>
              {voices.current < 0 && <option value={-1}>请选择…</option>}
              {voices.voices.map((v) => (
                <option key={v.slot} value={v.slot}>
                  {v.name}
                </option>
              ))}
            </select>
          ) : (
            <p className="muted">
              声音库是空的，<button className="link" onClick={() => goto("voices")}>去导入一个</button>
            </p>
          )}
          <VolumeSlider label="音量" value={status.audio.outputGain} onChange={(v) => set({ serverOutputAudioGain: v })} />
          {status.system.mirrored && (
            <p className="muted small">
              系统音量键已接管：{status.system.muted ? "已静音" : `${Math.round((status.system.volume ?? 1) * 100)}%`}
              （任务栏音量直接控制变声后的声音）
            </p>
          )}
          <label className="row switch-row">
            <span>
              临时听原声
              <small className="muted">不关闭变声，直接放老师原本的声音</small>
            </span>
            <input type="checkbox" className="switch" checked={status.passThrough} onChange={(e) => set({ passThrough: e.target.checked })} />
          </label>
        </section>

        <section className="card">
          <h3>状态</h3>
          <div className="stat-row">
            <span>输入音量</span>
            <span className="mono">{status.audio.inDb === null ? "—" : `${status.audio.inDb} dB`}</span>
          </div>
          <Meter db={status.audio.inDb} />
          <div className="stats">
            <div>
              <div className="stat-num">{p.latencyMs ?? "—"}</div>
              <div className="muted">延迟 ms（估算）</div>
            </div>
            <div>
              <div className="stat-num">{p.load === null ? "—" : `${Math.round(p.load * 100)}%`}</div>
              <div className="muted">GPU 负载</div>
            </div>
            <div>
              <div className="stat-num">{p.procMs ?? "—"}</div>
              <div className="muted">每块处理 ms</div>
            </div>
          </div>
          {p.peakLoad !== null && p.peakLoad > 0.9 && (
            <p className="bad small">负载峰值接近上限，可能出现爆音：去「设置」把延迟档位调到「稳定」。</p>
          )}
          {status.running && status.audio.xruns > 5 && (
            <p className="muted small">检测到 {status.audio.xruns} 次音频缓冲抖动，若听感卡顿可在「设置」切到「稳定」档。</p>
          )}
        </section>

        <section className="card wide">
          <h3>音高</h3>
          <label className="row switch-row">
            <span>
              自动匹配音高
              <small className="muted">根据老师的声音自动升降调，男老师换女声也自然</small>
            </span>
            <input type="checkbox" className="switch" checked={status.pitch.auto} onChange={(e) => set({ autoPitch: e.target.checked })} />
          </label>
          <div className="stats">
            <div>
              <div className="stat-num">{status.pitch.sourceHz ? `${Math.round(status.pitch.sourceHz)}` : "—"}</div>
              <div className="muted">老师音高 Hz</div>
            </div>
            <div>
              <div className="stat-num">{Math.round(status.pitch.targetHz)}</div>
              <div className="muted">目标音高 Hz</div>
            </div>
            <div>
              <div className="stat-num">{tranText}</div>
              <div className="muted">当前升降</div>
            </div>
          </div>
          {status.pitch.auto ? (
            <SmoothSlider
              label="目标音高"
              value={status.pitch.targetHz}
              min={100}
              max={450}
              step={5}
              format={(hz) => `${hz} Hz`}
              onCommit={saveTarget}
            />
          ) : (
            <SmoothSlider
              label="手动升降"
              value={status.pitch.tran}
              min={-24}
              max={24}
              step={1}
              format={fmtTran}
              onChange={(t) => set({ tran: t })}
            />
          )}
          <p className="muted small">
            声音太尖就把目标音高调低，太闷就调高。调好的值会保存在当前声音上，下次切换回来自动套用。
          </p>
        </section>
      </div>
    </div>
  );
}
