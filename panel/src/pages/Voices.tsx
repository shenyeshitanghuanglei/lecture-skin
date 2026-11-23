import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Status, Voice, VoiceList } from "../api";
import { attempt } from "../hooks";

interface Props {
  status: Status;
  refreshStatus: () => void;
  toast: (m: string, error?: boolean) => void;
}

function VoiceCard({
  v,
  active,
  onChange,
  toast,
}: {
  v: Voice;
  active: boolean;
  onChange: (l: VoiceList) => void;
  toast: (m: string, error?: boolean) => void;
}) {
  const err = (m: string) => toast(m, true);
  const [editing, setEditing] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [name, setName] = useState(v.name);
  const [hz, setHz] = useState(v.targetPitchHz || 0);
  const [ix, setIx] = useState(v.indexRatio || 0);
  const [prt, setPrt] = useState(v.protect ?? 0.33);

  const startEdit = () => {
    setName(v.name);
    setHz(v.targetPitchHz || 0);
    setIx(v.indexRatio || 0);
    setPrt(v.protect ?? 0.33);
    setEditing(true);
  };

  const save = async () => {
    const patch = v.hasIndex
      ? { name, targetPitchHz: hz, indexRatio: ix, protect: prt }
      : { name, targetPitchHz: hz, protect: prt };
    const l = await attempt(() => api.patchVoice(v.slot, patch), err);
    if (l) {
      onChange(l);
      setEditing(false);
      toast("已保存");
    }
  };
  const use = async () => {
    setSwitching(true);
    const l = await attempt(() => api.selectVoice(v.slot), err);
    setSwitching(false);
    if (l) {
      onChange(l);
      toast(`已切换到「${v.name}」`);
    }
  };
  const remove = async () => {
    if (!window.confirm(`删除声音「${v.name}」？模型文件会一起删除，无法恢复。`)) return;
    const l = await attempt(() => api.deleteVoice(v.slot), err);
    if (l) {
      onChange(l);
      toast("已删除");
    }
  };

  return (
    <div className={"voice" + (active ? " active" : "")}>
      <div className="voice-avatar">
        {v.hasIcon ? <img src={`/vt/api/voices/${v.slot}/icon`} alt="" /> : <span>{v.name.slice(0, 1)}</span>}
      </div>
      <div className="voice-body">
        {editing ? (
          <div className="edit-grid">
            <label>
              名字
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              目标音高 Hz
              <input type="number" min={0} max={600} value={hz} onChange={(e) => setHz(Number(e.target.value))} />
            </label>
            {v.hasIndex && (
              <label>
                索引强度 0–1 <small className="muted">越高越像原声，太高可能含糊</small>
                <input type="number" min={0} max={1} step={0.1} value={ix} onChange={(e) => setIx(Number(e.target.value))} />
              </label>
            )}
            <label>
              咬字保护 0–0.5 <small className="muted">推荐 0.33（0.5 为关闭）</small>
              <input type="number" min={0} max={0.5} step={0.01} value={prt} onChange={(e) => setPrt(Number(e.target.value))} />
            </label>
          </div>
        ) : (
          <>
            <div className="voice-name">
              {v.name} {active && <span className="tag">使用中</span>}
            </div>
            <div className="muted small">
              {v.sampleRate ? `${v.sampleRate / 1000}k` : "?"} · RVC {v.version}
              {v.hasIndex ? ` · 索引 ${v.indexRatio}` : ""} · 目标音高 {v.targetPitchHz ? `${Math.round(v.targetPitchHz)} Hz` : "未设置"}
            </div>
          </>
        )}
      </div>
      <div className="voice-actions">
        {editing ? (
          <>
            <button onClick={save}>保存</button>
            <button className="ghost" onClick={() => setEditing(false)}>
              取消
            </button>
          </>
        ) : (
          <>
            {!active && (
              <button onClick={use} disabled={switching}>
                {switching ? "切换中…" : "使用"}
              </button>
            )}
            <button className="ghost" onClick={startEdit}>
              编辑
            </button>
            {!active && (
              <button className="ghost danger" onClick={remove}>
                删除
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function Voices({ status, refreshStatus, toast }: Props) {
  const err = (m: string) => toast(m, true);
  const [list, setList] = useState<VoiceList | null>(null);
  const [uploading, setUploading] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    attempt(api.voices, err).then((l) => l && setList(l));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onChange = (l: VoiceList) => {
    setList(l);
    refreshStatus();
  };

  const onImport = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const model = form.get("model") as File | null;
    if (!model || !model.name) return err("请选择模型文件（.pth / .safetensors / .onnx）");
    const idx = form.get("index") as File | null;
    if (idx && !idx.name) form.delete("index");
    if (!form.get("name")) form.set("name", model.name.replace(/\.(pth|safetensors|onnx)$/i, ""));
    setUploading(true);
    const l = await attempt(() => api.importVoice(form), err);
    setUploading(false);
    if (l) {
      setList(l);
      formRef.current?.reset();
      toast("导入成功，可以点「使用」切换过去了");
    }
  };

  return (
    <div className="page">
      <h2>声音库</h2>
      <section className="card">
        {!list ? (
          <p className="muted">加载中…</p>
        ) : list.voices.length === 0 ? (
          <p className="muted">还没有声音，在下面导入一个 RVC 模型吧。</p>
        ) : (
          <div className="voice-list">
            {list.voices.map((v) => (
              <VoiceCard key={v.slot} v={v} active={v.slot === list.current} onChange={onChange} toast={toast} />
            ))}
          </div>
        )}
      </section>

      <section className="card">
        <h3>导入 RVC 模型</h3>
        <form ref={formRef} className="import-form" onSubmit={onImport}>
          <label>
            模型文件 <small className="muted">.pth / .safetensors / .onnx</small>
            <input type="file" name="model" accept=".pth,.safetensors,.onnx" />
          </label>
          <label>
            索引文件（可选） <small className="muted">.index，能让音色更像</small>
            <input type="file" name="index" accept=".index" />
          </label>
          <label>
            名字
            <input name="name" placeholder="默认用文件名" />
          </label>
          <label>
            目标音高 Hz（可选）
            <input name="targetPitchHz" type="number" min={0} max={600} placeholder="女声约 230–300，男声约 100–150" />
          </label>
          <button type="submit" disabled={uploading}>
            {uploading ? "导入中…" : "导入"}
          </button>
        </form>
        <p className="muted small">
          请从可信来源导入 RVC 模型和索引文件。
        </p>
      </section>
      {status.voice === null && <p className="muted">提示：导入后点「使用」，再回主页开始变声。</p>}
    </div>
  );
}
