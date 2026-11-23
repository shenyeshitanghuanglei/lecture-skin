// Typed client for /vt/api/clone (voice cloning jobs).
import { request, VoiceList } from "./api";

export type JobState =
  | "new"
  | "draft"
  | "queued"
  | "preparing"
  | "prepared"
  | "training"
  | "trained"
  | "done"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface CloneFile {
  name: string;
  label: string;
  kind: "audio" | "video" | "record";
  separate: boolean;
  bytes?: number;
  seconds?: number;
  error?: string;
}

export interface Report {
  clips: number;
  used: number;
  seconds: number;
  f0: number;
  level: "good" | "warn" | "bad";
  canTrain: boolean;
  warnings: string[];
}

export interface Clip {
  id: string;
  file: string;
  start: number;
  dur: number;
  f0?: number;
  voiced?: number;
  clipped?: boolean;
  excluded: boolean;
  auto: string | null;
}

export interface LossPoint {
  epoch: number;
  mel: number;
  kl: number;
  fm: number;
  gen: number;
  disc: number;
}

export interface Checkpoint {
  epoch: number;
  mel: number;
  preview: string | null;
}

export interface Progress {
  state: JobState;
  running: string | null;
  stage?: string;
  pct?: number;
  message?: string;
  error?: string | null;
  epoch?: number;
  epochs?: number;
  etaSec?: number;
  epochSec?: number;
  preset?: string;
  losses?: LossPoint[];
  checkpoints?: Checkpoint[];
  previewShift?: number;
}

export interface JobMeta {
  name: string;
  createdAt: number;
  files: CloneFile[];
  previewFile?: string | null;
  previewLabel?: string | null;
  consent?: boolean;
  preset?: string;
  voiceSlot?: number | null;
  voiceSlotName?: string;
  exportedEpoch?: number;
  conversionPaused?: boolean;
  materialsDirty?: boolean;
}

export interface JobDetail {
  id: string;
  meta: JobMeta;
  progress: Progress;
  report: Report | null;
  clips: Clip[];
  hasPreviewSource: boolean;
  log: string;
  busy: string | null;
}

export interface JobSummary {
  id: string;
  name: string;
  createdAt: number;
  state: JobState;
  running: string | null;
  pct: number | null;
  voiceSlot: number | null;
  report: Report | null;
}

const jobs = "/clone/jobs";
const j = (id: string) => `${jobs}/${encodeURIComponent(id)}`;

export const cloneApi = {
  list: () => request<{ jobs: JobSummary[]; busy: string | null }>("GET", jobs, undefined, 3500),
  create: (name: string) => request<JobDetail>("POST", jobs, { name }),
  get: (id: string) => request<JobDetail>("GET", j(id), undefined, 3500),
  rename: (id: string, name: string) => request<JobDetail>("PATCH", j(id), { name }),
  remove: (id: string) => request<{ jobs: JobSummary[] }>("DELETE", j(id)),
  upload: (id: string, files: Blob[] | FileList, kind: "file" | "record" = "file", filename?: string) => {
    const form = new FormData();
    form.set("kind", kind);
    Array.from(files as ArrayLike<Blob>).forEach((f) => form.append("files", f, filename ?? (f as File).name));
    return request<JobDetail>("POST", `${j(id)}/files`, form);
  },
  patchFile: (id: string, name: string, patch: { separate?: boolean; label?: string }) =>
    request<JobDetail>("PATCH", `${j(id)}/files/${encodeURIComponent(name)}`, patch),
  deleteFile: (id: string, name: string) => request<JobDetail>("DELETE", `${j(id)}/files/${encodeURIComponent(name)}`),
  setPreviewSource: (id: string, file: File) => {
    const form = new FormData();
    form.set("file", file);
    return request<JobDetail>("POST", `${j(id)}/preview-source`, form);
  },
  clearPreviewSource: (id: string) => request<JobDetail>("DELETE", `${j(id)}/preview-source`),
  prepare: (id: string) => request<JobDetail>("POST", `${j(id)}/prepare`),
  setClips: (id: string, ids: string[], excluded: boolean) =>
    request<JobDetail>("PATCH", `${j(id)}/clips`, { ids, excluded }),
  train: (id: string, preset: string, consent: boolean, fresh = false) =>
    request<JobDetail>("POST", `${j(id)}/train`, { preset, consent, fresh }),
  cancel: (id: string) => request<JobDetail>("POST", `${j(id)}/cancel`),
  finalize: (id: string, epoch: number, name: string) =>
    request<VoiceList & { slot: number; job: JobDetail }>("POST", `${j(id)}/finalize`, { epoch, name }),
  clipUrl: (id: string, clip: string) => `/vt/api${j(id)}/clips/${clip}.wav`,
  previewUrl: (id: string, fname: string, bust = "") => `/vt/api${j(id)}/preview/${fname}${bust ? `?v=${bust}` : ""}`,
};
