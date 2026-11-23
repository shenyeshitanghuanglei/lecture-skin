import { useEffect, useRef, useState } from "react";
import { api, Devices, Status } from "../api";
import { attempt } from "../hooks";

interface Props {
  status: Status;
  setStatus: (s: Status) => void;
  toast: (m: string, error?: boolean) => void;
}

const PRESET_HINT: Record<string, string> = {
  low: "约 64ms 一块，最跟嘴型；电脑忙时可能爆音",
  balanced: "约 85ms 一块，推荐",
  stable: "约 170ms 一块，最不容易卡",
};

function DiagnosticsCard({ toast }: { toast: Props["toast"] }) {
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<string | null>(null);
  const err = (m: string) => toast(m, true);

  const exportBundle = async () => {
    setBusy(true);
    const r = await attempt(api.exportDiagnostics, err);
    setBusy(false);
    if (r) {
      setFile(r.path);
      toast("诊断信息已导出，已在文件夹中选中");
    }
  };

  return (
    <section className="card">
      <h3>问题反馈</h3>
      <p className="muted small">
        遇到问题时，先导出诊断信息，再到反馈页描述问题并附上这个 zip。诊断包含版本、显卡和内存、日志和设置，
        用户名已隐去，不含录音或声音模型。
      </p>
      <div className="row" style={{ gap: 8, justifyContent: "flex-start" }}>
        <button onClick={exportBundle} disabled={busy}>
          {busy ? "正在导出…" : "导出诊断信息"}
        </button>
        <button className="ghost" onClick={() => attempt(() => api.openOnboardingUrl("report_issue"), err)}>
          打开反馈页
        </button>
      </div>
      {file && <p className="muted small">已保存：{file}</p>}
    </section>
  );
}

/** Drag-safe throttled range slider: keeps local thumb state while dragging (with PointerCapture
 *  so releasing outside the track never gets stuck), eliminating HTTP poll rubber-banding. */
export function SmoothSlider({
  label,
  value,
  min,
  max,
  step,
  format,
  liveThrottleMs = 120,
  onChange,
  onCommit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  liveThrottleMs?: number;
  onChange?: (v: number) => void;
  onCommit?: (v: number) => void;
}) {
  const [v, setV] = useState(value);
  const last = useRef(0);
  const dragging = useRef(false);
  useEffect(() => {
    if (!dragging.current) setV(value);
  }, [value]);

  const push = (x: number, force = false) => {
    if (!onChange) return;
    const now = Date.now();
    if (force || now - last.current > liveThrottleMs) {
      last.current = now;
      onChange(x);
    }
  };
  const finish = (x: number) => {
    if (!dragging.current) return;
    dragging.current = false;
    push(x, true);
    onCommit?.(x);
  };

  return (
    <div className="slider-row">
      <span>{label}</span>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={v}
        onPointerDown={(e) => {
          dragging.current = true;
          try {
            e.currentTarget.setPointerCapture(e.pointerId);
          } catch {
            /* synthetic event */
          }
        }}
        onChange={(e) => {
          const x = Number(e.target.value);
          setV(x);
          push(x);
        }}
        onPointerUp={(e) => finish(Number(e.currentTarget.value))}
        onPointerCancel={(e) => finish(Number(e.currentTarget.value))}
        onLostPointerCapture={(e) => finish(Number(e.currentTarget.value))}
        onKeyUp={(e) => {
          const x = Number(e.currentTarget.value);
          push(x, true);
          onCommit?.(x);
        }}
      />
      <span className="mono">{format(v)}</span>
    </div>
  );
}

/** Live volume slider: applies while dragging (throttled), shows percentage. */
export function VolumeSlider({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <SmoothSlider
      label={label}
      value={value}
      min={0}
      max={4}
      step={0.05}
      format={(v) => `${Math.round(v * 100)}%`}
      onChange={onChange}
    />
  );
}

export default function Settings({ status, setStatus, toast }: Props) {
  const err = (m: string) => toast(m, true);
  const [devices, setDevices] = useState<Devices | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [input, setInput] = useState(status.audio.inputDevice);
  const [output, setOutput] = useState(status.audio.outputDevice);
  const [showAll, setShowAll] = useState(false);
  const [manual, setManual] = useState(false);

  const loadDevices = async (rescan = false) => {
    if (rescan) setRefreshing(true);
    const d = await attempt(() => api.devices(rescan), err);
    if (rescan) setRefreshing(false);
    if (!d) return;
    setDevices(d);
    setInput(d.selected.input);
    setOutput(d.selected.output);
    if (rescan) toast("设备列表已刷新");
  };

  useEffect(() => {
    loadDevices(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filter = <T extends { hostApi: string; id: number }>(list: T[], keep: number) =>
    showAll ? list : list.filter((d) => d.hostApi.includes("WASAPI") || d.id === keep);

  const applyDevices = async () => {
    const d = await attempt(() => api.setDevices(input, output), err);
    if (d) {
      setDevices(d);
      toast("设备已更新");
    }
  };

  const set = async (values: Record<string, unknown>) => {
    const s = await attempt(() => api.setSettings(values), err);
    if (s) setStatus(s);
  };

  const setPreset = async (preset: string) => {
    const s = await attempt(() => api.setLatency(preset), err);
    if (s) {
      setStatus(s);
      toast(`已切换到「${s.perf.presets[preset]}」`);
    }
  };

  const changed = devices && (input !== devices.selected.input || output !== devices.selected.output);
  const cableIn = devices?.inputs.find((d) => d.id === input)?.isCable;
  // Auto-pick (CABLE in, system default out) covers most setups; only show the pickers when asked or something looks off.
  const needsAttention = !!devices && (devices.warnings.length > 0 || !devices.inputs.find((d) => d.id === devices.selected.input)?.isCable);
  const showPickers = manual || needsAttention;
  const inName = devices?.inputs.find((d) => d.id === devices.selected.input)?.name ?? "未选择";
  const outName = !devices
    ? ""
    : devices.selected.output === -2
      ? `跟随系统默认（${devices.systemDefaultOutput ?? "未知"}）`
      : devices.outputs.find((d) => d.id === devices.selected.output)?.name ?? "未选择";

  return (
    <div className="page">
      <h2>设置</h2>

      <section className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>音频设备</h3>
          <button
            className="ghost small-btn"
            onClick={() => loadDevices(true)}
            disabled={refreshing || status.running}
            title={status.running ? "变声运行中不能重新扫描硬件，停止后可刷新" : "插拔耳机后点这里重新扫描"}
          >
            {refreshing ? "扫描中…" : "↻ 刷新设备"}
          </button>
        </div>
        {!devices ? (
          <p className="muted">读取设备中…</p>
        ) : !showPickers ? (
          <>
            <p className="small">
              输入：{inName}
              <br />
              输出：{outName}
            </p>
            <p className="muted small">
              已自动选好，一般无需改动。
              <button className="link" onClick={() => setManual(true)}>
                手动选择设备
              </button>
            </p>
          </>
        ) : (
          <>
            {devices.warnings.map((w) => (
              <p key={w} className="bad small">
                {w}
              </p>
            ))}
            <label>
              输入（老师的声音从哪来）
              <select value={input} onChange={(e) => setInput(Number(e.target.value))}>
                {filter(devices.inputs, input).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                    {showAll ? ` [${d.hostApi}]` : ""}
                  </option>
                ))}
              </select>
            </label>
            {!cableIn && <p className="bad small">输入一般应选「CABLE Output (VB-Audio Virtual Cable)」。</p>}
            <label>
              输出（变声后从哪播放）
              <select value={output} onChange={(e) => setOutput(Number(e.target.value))}>
                <option value={-2}>跟随系统默认输出（推荐：系统音量键可直接调）</option>
                {filter(devices.outputs, output).filter((d) => !d.isCable).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                    {showAll ? ` [${d.hostApi}]` : ""}
                  </option>
                ))}
              </select>
            </label>
            <div className="row">
              <label className="inline">
                <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> 显示所有接口（MME、DirectSound…）
              </label>
              <span className="row" style={{ gap: 8 }}>
                {manual && !needsAttention && (
                  <button className="ghost" onClick={() => { setManual(false); setInput(devices.selected.input); setOutput(devices.selected.output); }}>
                    收起
                  </button>
                )}
                <button onClick={applyDevices} disabled={!changed}>
                  应用
                </button>
              </span>
            </div>
          </>
        )}
        <div className="callout">
          <b>接线方法：</b> Windows「应用音量和设备首选项」里，把浏览器的输出设为 <b>CABLE Input</b>；系统默认输出保持你的耳机。
          <button className="link" onClick={() => attempt(api.openSoundSettings, err)}>
            打开该设置页
          </button>
        </div>
      </section>

      <section className="card">
        <h3>延迟档位</h3>
        <div className="segmented">
          {Object.entries(status.perf.presets).map(([k, label]) => (
            <button key={k} className={status.perf.preset === k ? "active" : ""} onClick={() => setPreset(k)}>
              {label}
              <small>{PRESET_HINT[k]}</small>
            </button>
          ))}
        </div>
        {status.perf.preset === null && <p className="muted small">当前是自定义块大小（{status.perf.blockMs} ms）。</p>}
      </section>

      <section className="card">
        <h3>音量</h3>
        <p className="muted small">变声后的声音已自动和原声一样响；这里在此基础上再调大调小。</p>
        <VolumeSlider label="输出音量" value={status.audio.outputGain} onChange={(v) => set({ serverOutputAudioGain: v })} />
      </section>

      <section className="card">
        <h3>显卡</h3>
        <select value={status.gpu.selected} onChange={(e) => set({ gpu: Number(e.target.value) })}>
          {status.gpu.devices.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <p className="muted small">切换显卡会重新加载模型，需要几秒钟。</p>
      </section>

      <DiagnosticsCard toast={toast} />
    </div>
  );
}
