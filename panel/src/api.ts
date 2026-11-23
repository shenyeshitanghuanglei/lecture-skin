// Typed client for the voice changer's /vt/api endpoints.

export interface Voice {
  slot: number;
  name: string;
  sampleRate: number | null;
  hasIndex: boolean;
  targetPitchHz: number;
  indexRatio: number;
  protect: number;
  version: string;
  credit: string;
  termsOfUseUrl: string;
  hasIcon: boolean;
}

export interface Status {
  cloneEnabled: boolean;
  running: boolean;
  passThrough: boolean;
  originalAudio: boolean;
  voice: Voice | null;
  pitch: { auto: boolean; tran: number; targetHz: number; sourceHz: number | null };
  audio: {
    inputDevice: number;
    outputDevice: number;
    sampleRate: number;
    inDb: number | null;
    inputGain: number;
    outputGain: number;
    matchGain: number;
    xruns: number;
    recoveries?: number;
  };
  perf: {
    blockMs: number;
    procMs: number | null;
    load: number | null;
    peakLoad: number | null;
    latencyMs: number | null;
    preset: string | null;
    presets: Record<string, string>;
  };
  gpu: { selected: number; devices: { id: number; name: string; backend: string }[] };
  warnings: string[];
  system: {
    defaultOutput: string | null;
    defaultIsCable: boolean;
    volume: number | null;
    muted: boolean;
    mirrored: boolean;
  };
}

export interface Device {
  id: number;
  name: string;
  hostApi: string;
  isCable: boolean;
}

export interface Devices {
  inputs: Device[];
  outputs: Device[];
  selected: { input: number; output: number }; // output -2 = follow Windows default
  warnings: string[];
  systemDefaultOutput: string | null;
}

export interface VoiceList {
  current: number;
  voices: Voice[];
}

export interface OnboardingInstallTask {
  status: "idle" | "running" | "success" | "error";
  step: string;
  message: string;
  logs: string[];
}

export interface OnboardingEnv {
  python: {
    installed: boolean;
    ok: boolean;
    version: string;
    required: string;
  };
  uv: {
    installed: boolean;
    version: string | null;
  };
  vbCable: {
    installed: boolean;
    deviceName: string | null;
    downloadUrl: string;
  };
  voiceCount: number;
  hasActiveVoice: boolean;
  installTask: OnboardingInstallTask;
}

export class ApiError extends Error {}

export async function request<T>(method: string, path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  const ctrl = timeoutMs ? new AbortController() : null;
  const timer = ctrl ? window.setTimeout(() => ctrl.abort(), timeoutMs) : undefined;
  const init: RequestInit = { method, signal: ctrl?.signal };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  try {
    const res = await fetch(`/vt/api${path}`, init);
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      /* plain-text error page */
    }
    if (!res.ok) {
      const d = data as { detail?: unknown } | null;
      const detail = d && typeof d.detail === "string" ? d.detail : `请求失败（${res.status}）`;
      throw new ApiError(detail);
    }
    return data as T;
  } finally {
    if (timer !== undefined) window.clearTimeout(timer);
  }
}

export const api = {
  status: (timeoutMs = 3500) => request<Status>("GET", "/status", undefined, timeoutMs),
  start: () => request<Status>("POST", "/start"),
  stop: () => request<Status>("POST", "/stop"),
  setSettings: (values: Record<string, unknown>) => request<Status>("POST", "/settings", { values }),
  setLatency: (preset: string) => request<Status>("POST", "/latency", { preset }),
  voices: () => request<VoiceList>("GET", "/voices"),
  selectVoice: (slot: number) => request<VoiceList>("POST", "/voices/select", { slot }),
  patchVoice: (slot: number, patch: { name?: string; targetPitchHz?: number; indexRatio?: number; protect?: number }) =>
    request<VoiceList>("PATCH", `/voices/${slot}`, patch),
  deleteVoice: (slot: number) => request<VoiceList>("DELETE", `/voices/${slot}`),
  importVoice: (form: FormData) => request<VoiceList & { slot: number }>("POST", "/voices/import", form),
  devices: (refresh = false) => request<Devices>("GET", `/devices${refresh ? "?refresh=true" : ""}`),
  setDevices: (input: number, output: number) => request<Devices>("POST", "/devices", { input, output }),
  openSoundSettings: (page: "apps-volume" | "sound" = "apps-volume") =>
    request<{ ok: boolean }>("POST", `/open-sound-settings?page=${page}`),
  onboardingEnv: (refresh = false) =>
    request<OnboardingEnv>("GET", `/onboarding/env${refresh ? "?refresh=true" : ""}`),
  ensureOnboardingEnv: () => request<OnboardingEnv>("POST", "/onboarding/ensure-env"),
  openOnboardingUrl: (target: "vb_cable") =>
    request<{ ok: boolean; url: string }>("POST", "/onboarding/open-url", { target }),
};
