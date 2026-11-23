import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { Status } from "../api";
import { attempt } from "../hooks";
import { Checkpoint, Clip, CloneFile, cloneApi, JobDetail, JobSummary, LossPoint } from "../cloneApi";

interface Props {
  status: Status;
  refreshStatus: () => void;
  toast: (m: string, error?: boolean) => void;
  gotoVoices: () => void;
}

const PRESETS = [
  { id: "fast", label: "快速", epochs: 100, hint: "先听听效果" },
  { id: "standard", label: "标准", epochs: 200, hint: "推荐" },
  { id: "fine", label: "精细", epochs: 400, hint: "素材多时更像" },
];
const ACCEPT = ".wav,.mp3,.flac,.m4a,.aac,.ogg,.opus,.wma,.webm,.mp4,.mkv,.flv,.mov,.avi,.ts,.m4v,.wmv";
const STORE_KEY = "vt.clone.job";

const STATE_LABEL: Record<string, string> = {
  new: "新建",
  draft: "待处理",
  queued: "排队中",
  preparing: "处理素材中",
  prepared: "素材就绪",
  training: "训练中",
  trained: "训练完成",
  done: "已入库",
  failed: "出错",
  cancelled: "已取消",
  interrupted: "已中断",
};

function fmtDur(sec: number) {
  if (!isFinite(sec) || sec <= 0) return "0 秒";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  if (m >= 60) return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
  return m ? `${m} 分 ${s} 秒` : `${s} 秒`;
}

function fmtBytes(n?: number) {
  if (!n) return "";
  return n > 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`;
}

function planEpochs(base: number, seconds: number) {
  const scale = Math.min(2, Math.max(0.5, Math.sqrt(600 / Math.max(seconds, 60))));
  return Math.round((base * scale) / 10) * 10;
}

function readStored(): string | null {
  try {
    return localStorage.getItem(STORE_KEY);
  } catch {
    return null;
  }
}
function store(id: string | null) {
  try {
    if (id) localStorage.setItem(STORE_KEY, id);
    else localStorage.removeItem(STORE_KEY);
  } catch {
    /* private mode */
  }
}

/** One shared <audio> so starting a clip stops the previous one. */
function usePlayer() {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  useEffect(() => () => ref.current?.pause(), []);
  const toggle = useCallback(
    (url: string) => {
      if (!ref.current) {
        ref.current = new Audio();
        ref.current.onended = () => setPlaying(null);
        ref.current.onerror = () => setPlaying(null);
      }
      const a = ref.current;
      if (playing === url) {
        a.pause();
        setPlaying(null);
        return;
      }
      a.src = url;
      a.play().catch(() => setPlaying(null));
      setPlaying(url);
    },
    [playing],
  );
  return { playing, toggle };
}

function PlayButton({ url, player, label }: { url: string; player: ReturnType<typeof usePlayer>; label?: string }) {
  const on = player.playing === url;
  return (
    <button className={"play" + (on ? " on" : "")} onClick={() => player.toggle(url)} title={on ? "停止" : "播放"}>
      {on ? "■" : "▶"}
      {label && <span>{label}</span>}
    </button>
  );
}

// ------------------------------------------------------------------ recorder
function Recorder({ onDone, disabled, toast }: { onDone: (b: Blob) => void; disabled: boolean; toast: Props["toast"] }) {
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [secs, setSecs] = useState(0);
  const timer = useRef<number>();
  const session = useRef<{ stream?: MediaStream } | null>(null);

  const start = async () => {
    // Ask for this recording even if a browser profile already allows microphone access.
    if (session.current || !window.confirm("允许本次使用麦克风录音？录音会保存到这台电脑的克隆作业目录。")) return;
    const current: { stream?: MediaStream } = {};
    session.current = current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
      if (session.current !== current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      current.stream = stream;
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      const r = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 128000 } : undefined);
      const chunks: Blob[] = [];
      r.ondataavailable = (ev) => ev.data.size && chunks.push(ev.data);
      r.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        if (session.current !== current) return;
        session.current = null;
        window.clearInterval(timer.current);
        setRec(null);
        const blob = new Blob(chunks, { type: r.mimeType || "audio/webm" });
        if (blob.size > 0) onDone(blob);
      };
      r.start(1000);
      setRec(r);
      setSecs(0);
      const t0 = Date.now();
      timer.current = window.setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 500);
    } catch {
      if (session.current !== current) return;
      session.current = null;
      current.stream?.getTracks().forEach((track) => track.stop());
      toast("无法使用麦克风：请在弹出的提示里点「允许」，并检查 Windows 设置 → 隐私和安全性 → 麦克风 是否允许桌面应用访问", true);
    }
  };
  useEffect(() => () => {
    session.current?.stream?.getTracks().forEach((track) => track.stop());
    session.current = null;
    window.clearInterval(timer.current);
  }, []);

  return rec ? (
    <button className="rec on" onClick={() => rec.stop()}>
      <span className="rec-dot" /> 停止录音 {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")}
    </button>
  ) : (
    <button className="ghost rec" onClick={start} disabled={disabled}>
      <span className="rec-dot idle" /> 用麦克风录音
    </button>
  );
}

// ------------------------------------------------------------------ steps
function Materials({
  job,
  locked,
  toast,
  setJob,
}: {
  job: JobDetail;
  locked: boolean;
  toast: Props["toast"];
  setJob: (j: JobDetail) => void;
}) {
  const err = (m: string) => toast(m, true);
  const [uploading, setUploading] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const files = job.meta.files || [];

  const upload = async (list: FileList | Blob[], kind: "file" | "record" = "file") => {
    if (!list.length) return;
    setUploading(kind === "record" ? "正在保存录音…" : `正在上传 ${list.length} 个文件…`);
    const j = await attempt(() => cloneApi.upload(job.id, list, kind, kind === "record" ? "record.webm" : undefined), err);
    setUploading(null);
    if (j) setJob(j);
  };
  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) upload(e.target.files);
    e.target.value = "";
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    if (!locked) upload(e.dataTransfer.files);
  };
  const toggleSep = async (f: CloneFile) => {
    const j = await attempt(() => cloneApi.patchFile(job.id, f.name, { separate: !f.separate }), err);
    if (j) setJob(j);
  };
  const remove = async (f: CloneFile) => {
    const j = await attempt(() => cloneApi.deleteFile(job.id, f.name), err);
    if (j) setJob(j);
  };

  return (
    <section className="card">
      <h3>
        <span className="step-num">1</span>添加素材
      </h3>
      <p className="muted small" style={{ marginTop: -6 }}>
        同一个人说话的声音，总共 <b>10–30 分钟</b> 最好（至少 3 分钟）。可以是录音文件、视频（会自动抽出声音），或者直接录。
        只用你有权使用的声音。
      </p>
      <div
        className={"dropzone" + (drag ? " drag" : "") + (locked ? " locked" : "")}
        onDragOver={(e) => {
          e.preventDefault();
          if (!locked) setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        onClick={() => !locked && !uploading && input.current?.click()}
        role="button"
        tabIndex={locked ? -1 : 0}
        aria-disabled={locked || !!uploading}
        aria-label="添加音频或视频素材"
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          if (!locked && !uploading) input.current?.click();
        }}
      >
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={onPick} />
        <div className="dz-icon">⇪</div>
        <div>{uploading ?? "把音频或视频文件拖到这里，或点击选择"}</div>
        <div className="muted small">wav / mp3 / flac / m4a / mp4 / mkv / flv …</div>
      </div>
      <div className="row" style={{ marginTop: 10, justifyContent: "flex-start" }}>
        <Recorder onDone={(b) => upload([b], "record")} disabled={locked || !!uploading} toast={toast} />
        <span className="muted small">录音时请在安静的房间里，用平常讲话的语气，读书或聊天都可以。</span>
      </div>

      {files.length > 0 && (
        <div className="file-list">
          {files.map((f) => (
            <div className="file-row" key={f.name}>
              <span className="file-kind">{f.kind === "video" ? "🎬" : f.kind === "record" ? "🎙" : "♪"}</span>
              <div className="file-body">
                <div className="file-name" title={f.label}>
                  {f.label}
                </div>
                <div className="muted small">
                  {f.seconds ? fmtDur(f.seconds) : fmtBytes(f.bytes)}
                  {f.error && <span className="bad"> · {f.error}</span>}
                </div>
              </div>
              <label className="inline small sep-toggle" title="有背景音乐、音效或别人说话时打开（会慢一些）">
                <input type="checkbox" className="switch" checked={f.separate} disabled={locked} onChange={() => toggleSep(f)} />
                分离人声
              </label>
              <button className="ghost danger small-btn" disabled={locked} onClick={() => remove(f)}>
                移除
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ClipList({ job, player, locked, setJob, toast }: {
  job: JobDetail;
  player: ReturnType<typeof usePlayer>;
  locked: boolean;
  setJob: (j: JobDetail) => void;
  toast: Props["toast"];
}) {
  const [open, setOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const byFile = Object.fromEntries((job.meta.files || []).map((f) => [f.name, f.label]));
  const toggle = async (c: Clip) => {
    const j = await attempt(() => cloneApi.setClips(job.id, [c.id], !c.excluded), (m) => toast(m, true));
    if (j) setJob(j);
  };
  const clips = showAll ? job.clips : job.clips.slice(0, 60);
  return (
    <div className="clips">
      <button className="link" onClick={() => setOpen(!open)}>
        {open ? "收起片段" : `逐条试听 / 排除片段（${job.clips.length} 条）`}
      </button>
      {open && (
        <>
          <p className="muted small">点 ▶ 试听。有杂音、别人的声音、笑声或音乐的片段可以排除掉，训练会更干净。</p>
          <div className="clip-grid">
            {clips.map((c) => {
              const url = cloneApi.clipUrl(job.id, c.id);
              return (
                <div key={c.id} className={"clip" + (c.excluded ? " excluded" : "")} title={`${byFile[c.file] ?? ""} @ ${c.start}s`}>
                  <PlayButton url={url} player={player} />
                  <span className="mono-sm">{c.dur.toFixed(1)}s</span>
                  <span className="muted small">{c.f0 ? `${Math.round(c.f0)}Hz` : ""}</span>
                  {c.auto && <span className="muted small" title={c.auto}>⚠</span>}
                  <button className="link small" disabled={locked} onClick={() => toggle(c)}>
                    {c.excluded ? "恢复" : "排除"}
                  </button>
                </div>
              );
            })}
          </div>
          {!showAll && job.clips.length > 60 && (
            <button className="link" onClick={() => setShowAll(true)}>
              显示全部 {job.clips.length} 条
            </button>
          )}
        </>
      )}
    </div>
  );
}

function Review({ job, player, locked, otherBusy, setJob, toast, onPrepare, stale }: {
  job: JobDetail;
  player: ReturnType<typeof usePlayer>;
  locked: boolean;
  otherBusy: boolean;
  setJob: (j: JobDetail) => void;
  toast: Props["toast"];
  onPrepare: () => void;
  stale: boolean;
}) {
  const p = job.progress;
  const preparing = p.running === "prepare";
  const queuedPrepare = p.state === "queued" && p.stage === "prepare";
  const r = job.report;
  const hasFiles = (job.meta.files || []).length > 0;
  const cancel = async () => {
    const j = await attempt(() => cloneApi.cancel(job.id), (m) => toast(m, true));
    if (j) setJob(j);
  };
  return (
    <section className="card">
      <h3>
        <span className="step-num">2</span>处理与检查素材
      </h3>
      {preparing ? (
        <>
          <ProgressBar pct={p.pct ?? 0} text={p.message ?? "处理中…"} />
          <button className="ghost danger small-btn" onClick={cancel}>停止处理</button>
        </>
      ) : queuedPrepare ? (
        <div className="row">
          <span className="accent-text small">{p.message ?? "排队中，等待显卡空闲后自动处理素材…"}</span>
          <button className="ghost danger small-btn" onClick={cancel}>
            取消排队
          </button>
        </div>
      ) : (
        <div className="row" style={{ justifyContent: "flex-start" }}>
          <button onClick={onPrepare} disabled={locked || !hasFiles}>
            {otherBusy ? "排队处理素材" : r && !stale ? "重新处理素材" : "处理素材"}
          </button>
          <span className="muted small">
            {stale && r ? "素材有变动，需要重新处理。" : "会切成短句、统一音量、提取音高；勾了「分离人声」的文件还会去掉背景音乐。"}
          </span>
        </div>
      )}
      {p.state === "failed" && stale && p.error && <div className="callout error-callout">{p.error}</div>}
      {r && !preparing && (
        <div className={stale ? "stale" : ""}>
          <div className="stats" style={{ marginTop: 14 }}>
            <div>
              <div className="muted small">可用人声</div>
              <div className={"stat-num " + (r.level === "good" ? "ok" : r.level === "bad" ? "bad" : "warn-text")}>
                {fmtDur(r.seconds)}
              </div>
            </div>
            <div>
              <div className="muted small">片段</div>
              <div className="stat-num">
                {r.used}
                <small className="muted"> / {r.clips}</small>
              </div>
            </div>
            <div>
              <div className="muted small">平均音高</div>
              <div className="stat-num">{r.f0 ? `${Math.round(r.f0)} Hz` : "—"}</div>
            </div>
          </div>
          <div className="duration-bar">
            <div className={"duration-fill " + r.level} style={{ width: `${Math.min(100, (r.seconds / 1800) * 100)}%` }} />
            <span className="tick" style={{ left: "10%" }} title="3 分钟（最少）" />
            <span className="tick" style={{ left: "33.3%" }} title="10 分钟（推荐）" />
          </div>
          <div className="muted small duration-legend">
            <span>0</span>
            <span style={{ left: "10%" }}>3 分</span>
            <span style={{ left: "33.3%" }}>10 分</span>
            <span style={{ right: 0 }}>30 分</span>
          </div>
          {r.warnings.map((w) => (
            <div key={w} className="callout warn-callout">
              {w}
            </div>
          ))}
          <ClipList job={job} player={player} locked={locked} setJob={setJob} toast={toast} />
        </div>
      )}
    </section>
  );
}

function ProgressBar({ pct, text, sub }: { pct: number; text: string; sub?: string }) {
  return (
    <div>
      <div className="row">
        <span>{text}</span>
        <span className="muted small">{sub ?? `${Math.round(pct * 100)}%`}</span>
      </div>
      <div className="meter">
        <div className="meter-fill" style={{ width: `${Math.max(2, pct * 100)}%` }} />
      </div>
    </div>
  );
}

function LossChart({ losses }: { losses: LossPoint[] }) {
  if (losses.length < 2) return null;
  const w = 600;
  const h = 90;
  const ys = losses.map((l) => l.mel);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const span = hi - lo || 1;
  const pts = losses
    .map((l, i) => `${((i / (losses.length - 1)) * w).toFixed(1)},${(h - 6 - ((l.mel - lo) / span) * (h - 12)).toFixed(1)}`)
    .join(" ");
  return (
    <div className="loss">
      <div className="muted small row">
        <span>音色误差（mel loss，越低越像，降不动了就差不多了）</span>
        <span className="mono-sm">
          {ys[0].toFixed(1)} → {ys[ys.length - 1].toFixed(1)}
        </span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="loss-svg">
        <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

function Train({ job, locked, otherBusy, toast, setJob, status }: {
  job: JobDetail;
  locked: boolean;
  otherBusy: boolean;
  toast: Props["toast"];
  setJob: (j: JobDetail) => void;
  status: Status;
}) {
  const err = (m: string) => toast(m, true);
  const p = job.progress;
  const r = job.report;
  const training = p.running === "train";
  const queuedTrain = p.state === "queued" && p.stage === "train";
  const [preset, setPreset] = useState(job.meta.preset || "standard");
  const [consent, setConsent] = useState(!!job.meta.consent);
  const previewInput = useRef<HTMLInputElement>(null);
  const secs = r?.seconds ?? 0;
  // measured on an RTX 5070 Laptop: ~4.9 s per epoch for 3.6 min of speech at batch 8
  const epochSec = (p.epochSec && p.epochSec > 0 ? p.epochSec : 0) || Math.max(2, (secs / 216) * 4.9);
  const hasCkpt = !!p.checkpoints?.length;
  const resumable = (p.state === "interrupted" || p.state === "cancelled" || p.state === "failed") && hasCkpt && p.preset === preset;

  const start = async (fresh: boolean) => {
    if (!otherBusy && status.running && !window.confirm("训练需要独占显卡，会先停止实时变声（系统声音照常播放）。继续？")) return;
    const j = await attempt(() => cloneApi.train(job.id, preset, consent, fresh), err);
    if (j) setJob(j);
  };
  const cancel = async () => {
    if (!queuedTrain && !window.confirm("停止训练？已保存的版本会保留，之后可以接着训练。")) return;
    const j = await attempt(() => cloneApi.cancel(job.id), err);
    if (j) setJob(j);
  };
  const pickPreview = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    const j = await attempt(() => cloneApi.setPreviewSource(job.id, f), err);
    if (j) {
      setJob(j);
      toast("下次开始训练时会用这段声音生成试听");
    }
  };

  const ready = !!r?.canTrain && ["prepared", "trained", "done", "cancelled", "interrupted", "failed"].includes(p.state) && !!r;

  return (
    <section className="card">
      <h3>
        <span className="step-num">3</span>训练
      </h3>
      {training ? (
        <>
          <ProgressBar
            pct={p.pct ?? 0}
            text={p.message ?? "训练中…"}
            sub={p.etaSec ? `还剩约 ${fmtDur(p.etaSec)}` : undefined}
          />
          <div className="row">
            <span className="muted small">
              训练期间显卡被占用，不能实时变声。关掉面板窗口也会继续训练。
            </span>
            <button className="ghost danger" onClick={cancel}>
              停止训练
            </button>
          </div>
        </>
      ) : queuedTrain ? (
        <div className="row">
          <span className="accent-text small">{p.message ?? "排队中，等待前序作业完成后自动开始训练…"}</span>
          <button className="ghost danger small-btn" onClick={cancel}>
            取消排队
          </button>
        </div>
      ) : (
        <>
          <div className="segmented">
            {PRESETS.map((x) => {
              const ep = planEpochs(x.epochs, secs);
              return (
                <button key={x.id} className={preset === x.id ? "active" : ""} onClick={() => setPreset(x.id)} disabled={locked}>
                  {x.label}
                  <small>
                    {ep} 轮 · 约 {fmtDur(ep * epochSec)} · {x.hint}
                  </small>
                </button>
              );
            })}
          </div>
          <div className="row preview-src">
            <span className="small">
              试听句子：
              {job.meta.previewFile ? (
                <b>{job.meta.previewLabel || "自定义录音"}</b>
              ) : (
                <span className="muted">默认用系统朗读一句讲课的话（建议换成一小段真实讲课录音，更能听出效果）</span>
              )}
            </span>
            <span>
              <input ref={previewInput} type="file" hidden accept={ACCEPT} onChange={pickPreview} />
              <button className="ghost small-btn" onClick={() => previewInput.current?.click()} disabled={locked}>
                {job.meta.previewFile ? "更换" : "上传"}
              </button>
              {job.meta.previewFile && (
                <button
                  className="ghost small-btn"
                  disabled={locked}
                  onClick={async () => {
                    const j = await attempt(() => cloneApi.clearPreviewSource(job.id), err);
                    if (j) setJob(j);
                  }}
                >
                  用默认
                </button>
              )}
            </span>
          </div>
          <label className="inline consent">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span className="small">我确认素材是我本人的声音，或我已获得声音主人的许可；训练出的模型只用于个人用途。</span>
          </label>
          <div className="row" style={{ justifyContent: "flex-start", marginTop: 12 }}>
            {resumable && (
              <button onClick={() => start(false)} disabled={locked || !consent}>
                {otherBusy ? "排队接着训练" : `从第 ${p.checkpoints?.[p.checkpoints.length - 1]?.epoch} 轮接着训练`}
              </button>
            )}
            <button
              className={resumable ? "ghost" : ""}
              onClick={() => {
                if (hasCkpt && !window.confirm("重新训练会删除这个作业已有的版本和试听。继续？")) return;
                start(true);
              }}
              disabled={locked || !ready || !consent}
            >
              {otherBusy ? (hasCkpt ? "排队重训" : "排队训练") : hasCkpt ? "重新训练" : "开始训练"}
            </button>
            {!ready && <span className="muted small">先处理素材，可用人声至少 3 分钟。</span>}
          </div>
        </>
      )}
      {p.state === "failed" && p.error && <div className="callout error-callout">{p.error}</div>}
      {p.losses && p.losses.length > 1 && <LossChart losses={p.losses} />}
    </section>
  );
}

function Finish({ job, player, locked, toast, setJob, refreshStatus, gotoVoices }: {
  job: JobDetail;
  player: ReturnType<typeof usePlayer>;
  locked: boolean;
  toast: Props["toast"];
  setJob: (j: JobDetail) => void;
  refreshStatus: () => void;
  gotoVoices: () => void;
}) {
  const p = job.progress;
  const cps: Checkpoint[] = p.checkpoints ?? [];
  const last = cps.length ? cps[cps.length - 1].epoch : 0;
  const [pick, setPick] = useState<number>(job.meta.exportedEpoch ?? last);
  const [name, setName] = useState(job.meta.voiceSlotName || job.meta.name);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!cps.some((c) => c.epoch === pick)) setPick(last);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [last]);
  if (!cps.length) return null;
  const bust = String(p.epoch ?? "");

  const finalize = async () => {
    setBusy(true);
    const res = await attempt(() => cloneApi.finalize(job.id, pick, name), (m) => toast(m, true));
    setBusy(false);
    if (res) {
      setJob(res.job);
      refreshStatus();
      toast(`「${name}」已加入声音库`);
    }
  };

  return (
    <section className="card">
      <h3>
        <span className="step-num">4</span>试听并选一个版本
      </h3>
      <p className="muted small" style={{ marginTop: -6 }}>
        每隔一段保存一个版本。一般越往后越像，但训练太久也可能变得含糊、有杂音——挑听起来最自然的那个。
        {p.previewShift ? ` 试听已按音高差自动升降 ${p.previewShift > 0 ? "+" : ""}${p.previewShift} 个半音。` : ""}
      </p>
      <div className="ckpt-list">
        {job.hasPreviewSource && (
          <div className="ckpt ref">
            <PlayButton url={cloneApi.previewUrl(job.id, "source.wav", bust)} player={player} />
            <span>原句</span>
            <span className="muted small">转换前</span>
          </div>
        )}
        {cps.length > 0 && (
          <div className="ckpt ref">
            <PlayButton url={cloneApi.previewUrl(job.id, "e0.wav", bust)} player={player} />
            <span>底模</span>
            <span className="muted small">没训练时的样子</span>
          </div>
        )}
        {cps.map((c) => (
          <label key={c.epoch} className={"ckpt" + (pick === c.epoch ? " active" : "")}>
            {c.preview ? <PlayButton url={cloneApi.previewUrl(job.id, c.preview, bust)} player={player} /> : <span className="play-ph" />}
            <input type="radio" name="ckpt" checked={pick === c.epoch} onChange={() => setPick(c.epoch)} disabled={locked} />
            <span>第 {c.epoch} 轮</span>
            <span className="muted small mono-sm">误差 {c.mel.toFixed(1)}</span>
            {job.meta.exportedEpoch === c.epoch && job.meta.voiceSlot != null && <span className="tag">已入库</span>}
          </label>
        ))}
      </div>
      <div className="row finish-row">
        <label className="inline grow">
          <span className="small nowrap">声音名字</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
        </label>
        <button onClick={finalize} disabled={locked || busy || !name.trim()}>
          {busy ? "正在加入…" : job.meta.voiceSlot != null ? "用这个版本更新声音库" : "加入声音库"}
        </button>
      </div>
      {job.meta.voiceSlot != null && (
        <div className="callout">
          已在声音库里（{job.meta.voiceSlotName}）。
          <button className="link" onClick={gotoVoices}>
            去声音库使用
          </button>
          目标音高已按素材设置为 {job.report?.f0 ? Math.round(job.report.f0) : "?"} Hz，索引强度 0.5，可以在声音库里再调。
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ page
export default function Clone({ status, refreshStatus, toast, gotoVoices }: Props) {
  const err = (m: string) => toast(m, true);
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [sel, setSel] = useState(readStored);
  const selected = useRef(sel);
  const select = useCallback((id: string | null) => {
    selected.current = id;
    setSel(id);
  }, []);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const player = usePlayer();

  const loadList = useCallback(async () => {
    const l = await attempt(cloneApi.list, err);
    if (!l) return;
    setJobs(l.jobs);
    setBusyId(l.busy);
    if (!l.jobs.some((j) => j.id === selected.current)) select(l.jobs[0]?.id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [select]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    store(sel);
    setJob(null);
    let active = true;
    if (sel) attempt(() => cloneApi.get(sel), err).then((j) => {
      if (j && active && j.id === selected.current) setJob(j);
    });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel]);

  // poll while something is running or queued
  const running = !!job?.progress.running || job?.progress.state === "queued" || !!busyId;
  useEffect(() => {
    if (!sel || !running) return;
    let active = true;
    let pending = false;
    const t = window.setInterval(async () => {
      if (pending) return;
      pending = true;
      try {
        const j = await cloneApi.get(sel);
        if (!active || j.id !== selected.current) return;
        setJob(j);
        setBusyId(j.busy);
        if (!j.progress.running && j.progress.state !== "queued" && !j.busy) loadList();
      } catch {
        /* server restarting */
      } finally {
        pending = false;
      }
    }, 1500);
    return () => {
      active = false;
      window.clearInterval(t);
    };
  }, [sel, running, loadList]);

  const updateJob = (j: JobDetail) => {
    if (j.id === selected.current) setJob(j);
    setBusyId(j.busy);
    setJobs((list) => list?.map((x) => (x.id === j.id ? { ...x, name: j.meta.name, state: j.progress.state, report: j.report } : x)) ?? list);
  };

  const create = async () => {
    const n = newName.trim();
    if (!n) return err("先给这个声音起个名字");
    const j = await attempt(() => cloneApi.create(n), err);
    if (j) {
      setNewName("");
      setCreating(false);
      await loadList();
      select(j.id);
    }
  };
  const remove = async () => {
    if (!job) return;
    const extra = job.meta.voiceSlot != null ? "（已加入声音库的声音不受影响）" : "";
    if (!window.confirm(`删除克隆作业「${job.meta.name}」？素材、训练结果都会删除，无法恢复${extra}。`)) return;
    const l = await attempt(() => cloneApi.remove(job.id), err);
    if (l) {
      if (selected.current === job.id) select(null);
      loadList();
    }
  };
  const prepare = async () => {
    if (!job) return;
    const otherRunning = !!busyId && busyId !== job.id;
    if (!otherRunning && status.running && !window.confirm("处理素材需要显卡提取人声和音高，会先暂停实时变声（系统声音照常播放）。继续？")) return;
    const j = await attempt(() => cloneApi.prepare(job.id), err);
    if (j) updateJob(j);
  };

  const otherBusy = !!job && !!busyId && busyId !== job.id;
  // Only lock editing on THIS job while it is actively running or queued; other jobs remain editable & queueable!
  const locked = !!job?.progress.running || job?.progress.state === "queued";
  const stale = !!job && (!!job.meta.materialsDirty || ["new", "draft"].includes(job.progress.state));

  return (
    <div className="page clone-page">
      <div className="row">
        <h2>声音克隆</h2>
        <span className="muted small">用一段声音训练出你自己的变声模型（本机显卡训练，不上传）</span>
      </div>

      <section className="card job-bar">
        {jobs === null ? (
          <span className="muted">加载中…</span>
        ) : (
          <div className="job-tabs">
            {jobs.map((j) => (
              <button key={j.id} className={"job-tab" + (j.id === sel ? " active" : "")} onClick={() => select(j.id)}>
                <span>{j.name}</span>
                <small className={j.running ? "accent-text" : "muted"}>
                  {j.running ? (j.running === "train" ? "训练中" : "处理中") : STATE_LABEL[j.state] ?? j.state}
                </small>
              </button>
            ))}
            {creating || jobs.length === 0 ? (
              <span className="new-job">
                <input
                  autoFocus
                  placeholder="新声音的名字，比如「王老师」"
                  value={newName}
                  maxLength={40}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && create()}
                />
                <button onClick={create}>新建</button>
                {jobs.length > 0 && (
                  <button className="ghost" onClick={() => setCreating(false)}>
                    取消
                  </button>
                )}
              </span>
            ) : (
              <button className="ghost job-add" onClick={() => setCreating(true)}>
                ＋ 新建克隆
              </button>
            )}
          </div>
        )}
      </section>

      {otherBusy && (
        <div className="card warn small">
          另一个声音正在占用显卡处理中；你仍可以在这里上传素材、筛选片段、试听入库，或点击「排队处理 / 排队训练」自动顺延执行。
        </div>
      )}

      {job && job.id === sel && (
        <>
          <div className="row job-head">
            <div>
              <span className="job-title">{job.meta.name}</span>
              <span className="tag soft">{STATE_LABEL[job.progress.state] ?? job.progress.state}</span>
            </div>
            <button className="ghost danger small-btn" onClick={remove} disabled={!!job.progress.running}>
              删除作业
            </button>
          </div>
          {job.progress.state === "interrupted" && (
            <div className="card warn small">
              上次处理被中断了（可能是服务重启或电脑休眠）。{job.progress.checkpoints?.length ? "可以在下面接着训练。" : "请重新开始这一步。"}
            </div>
          )}
          <Materials job={job} locked={locked} toast={toast} setJob={updateJob} />
          {(job.meta.files || []).length > 0 && (
            <Review
              job={job}
              player={player}
              locked={locked}
              otherBusy={otherBusy}
              setJob={updateJob}
              toast={toast}
              onPrepare={prepare}
              stale={stale}
            />
          )}
          {job.report && !stale && (
            <Train job={job} locked={locked} otherBusy={otherBusy} toast={toast} setJob={updateJob} status={status} />
          )}
          <Finish
            job={job}
            player={player}
            locked={locked}
            toast={toast}
            setJob={updateJob}
            refreshStatus={refreshStatus}
            gotoVoices={gotoVoices}
          />
        </>
      )}
    </div>
  );
}
