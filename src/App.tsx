import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import {
  Activity,
  AlertTriangle,
  Bot,
  ChevronDown,
  CornerDownLeft,
  FileText,
  FolderKanban,
  Hash,
  LayoutGrid,
  Loader2,
  MessageSquare,
  PauseCircle,
  Pencil,
  Play,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  SearchCheck,
  Send,
  Settings,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  WifiOff,
  Wrench,
  X,
} from 'lucide-react';
import CladexBackground from './components/CladexBackground';

type ViewName = 'relays' | 'workgroups' | 'review' | 'live';
type ViewMode = 'simple' | 'advanced';
type ProfileType = 'Claude' | 'Codex';
type RelayType = 'claude' | 'codex';
type ReviewProvider = 'codex' | 'claude';
type ReviewJobStatus = 'queued' | 'running' | 'completed' | 'completed_with_warnings' | 'failed' | 'cancelled';
type FixRunStatus = 'queued' | 'running' | 'completed' | 'completed_with_warnings' | 'failed' | 'cancelled';
type ReviewActivityTab = 'active' | 'history' | 'fixes' | 'snapshots';

interface Profile {
  id: string;
  name: string;
  displayName?: string;
  technicalName?: string;
  type: ProfileType;
  relayType: RelayType;
  workspace: string;
  workspaceLabel?: string;
  status: 'Running' | 'Stopped';
  running: boolean;
  ready: boolean;
  state: 'idle' | 'working';
  provider?: string;
  model?: string;
  codexHome?: string;
  claudeConfigDir?: string;
  triggerMode?: string;
  effort?: string;
  botName?: string;
  allowDms?: boolean;
  discordChannel?: string;
  channelLabel?: string;
  statusText?: string;
  activeWorktree?: string;
  activeChannel?: string;
  sessionId?: string;
  stateNamespace?: string;
  operatorIds?: string;
  allowedUserIds?: string;
  allowedChannelIds?: string;
  allowedBotIds?: string;
  allowedChannelAuthorIds?: string;
  channelNoMentionAuthorIds?: string;
  channelHistoryLimit?: string;
  startupDmUserIds?: string;
  startupDmText?: string;
  startupChannelText?: string;
}

interface RuntimeInfo {
  apiBase: string;
  backendDir: string;
  frontendDir?: string;
  packaged: boolean;
  appVersion: string;
  remoteAccessProtected?: boolean;
  remoteAccessToken?: string;
}

interface DirectoryListResponse {
  currentPath: string;
  parentPath: string;
  directories: Array<{ name: string; path: string }>;
}

interface ProjectRecord {
  name: string;
  memberCount: number;
  members: Array<{ id: string; displayName: string; relayType: RelayType; workspace: string }>;
  missingMembers: Array<{ name: string; relayType: RelayType }>;
}

interface ReviewProgress {
  total: number;
  queued: number;
  running: number;
  done: number;
  failed: number;
  cancelled?: number;
  maxParallel?: number;
  maxWorkers?: number;
}

interface LimitMetadata {
  maxParallel?: number;
  maxWorkers?: number;
  warnings?: string[];
  accountHomeWarning?: string;
}

interface ReviewAnalysis {
  workspace: string;
  projectName: string;
  fileCount: number;
  workspaceBytes?: number;
  languages?: Array<{ name: string; files: number }>;
  markers?: Array<{ path: string; label: string }>;
  validationCommands?: string[];
  riskAreas?: string[];
  hasTests?: boolean;
  secretLikeFileCount?: number;
  selfReview?: boolean;
  recommendation: {
    provider: ReviewProvider;
    agents: number;
    title: string;
    modelStrategy?: string;
    laneFocuses?: Array<{ focus: string; detail: string }>;
    reasons?: string[];
    limits?: LimitMetadata;
  };
}

interface SeverityCounts {
  high: number;
  medium: number;
  low: number;
}

interface ReviewAgentRecord {
  id: string;
  provider: ReviewProvider;
  focus?: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  assignedFiles: number;
  findings: number;
  detail: string;
}

interface ReviewFinding {
  id?: string;
  severity?: 'high' | 'medium' | 'low';
  category?: string;
  path?: string;
  line?: number;
  title?: string;
  detail?: string;
  recommendation?: string;
  agentId?: string;
  seenByAgents?: string[];
}

interface ReviewJob {
  id: string;
  title: string;
  workspace: string;
  provider: ReviewProvider;
  strategy?: string;
  preflightOnly?: boolean;
  selfReview?: boolean;
  agentCount: number;
  accountHome?: string;
  status: ReviewJobStatus;
  cancelRequested?: boolean;
  createdAt: string;
  updatedAt: string;
  startedAt?: string;
  finishedAt?: string;
  progress: ReviewProgress;
  agents: ReviewAgentRecord[];
  artifactDir: string;
  reportPath: string;
  findingsPath: string;
  fixPlanPath?: string;
  sourceBackup?: { id?: string; error?: string };
  reportPreview?: string;
  severityCounts?: SeverityCounts;
  maxParallel?: number;
  maxWorkers?: number;
  maxAgents?: number;
  limitWarnings?: string[];
  warnings?: string[];
  limits?: LimitMetadata;
  error?: string;
}

interface FixTaskRecord {
  id: string;
  title?: string;
  status: 'queued' | 'running' | 'done' | 'completed' | 'completed_with_warnings' | 'failed' | 'cancelled';
  detail?: string;
  files?: string[];
  provider?: string;
  reasoningEffort?: string;
  rationale?: string;
  dependsOn?: string[];
  findingIds?: string[];
  phase?: number;
  severity?: string;
  category?: string;
  recommendation?: string;
  error?: string;
}

interface FixRun {
  id: string;
  reviewId?: string;
  reviewJobId?: string;
  title?: string;
  workspace: string;
  provider?: ReviewProvider;
  status: FixRunStatus;
  cancelRequested?: boolean;
  createdAt: string;
  updatedAt: string;
  startedAt?: string;
  finishedAt?: string;
  progress?: ReviewProgress;
  tasks?: FixTaskRecord[];
  taskCount?: number;
  artifactDir?: string;
  reportPath?: string;
  sourceBackup?: { id?: string; error?: string };
  backup?: { id?: string; error?: string };
  restoreCommand?: string;
  selfReview?: boolean;
  selfFix?: boolean;
  maxParallel?: number;
  maxWorkers?: number;
  maxAgents?: number;
  requestedMaxAgents?: number;
  plan?: FixRunPlan;
  limitWarnings?: string[];
  warnings?: string[];
  limits?: LimitMetadata;
  error?: string;
}

interface FixRunPlan {
  source?: 'ai' | 'deterministic' | string;
  provider?: string;
  summary?: string;
  rationale?: string;
  recommendedAgentCount?: number;
  taskCount?: number;
  fallbackReason?: string;
}

interface BackupRecord {
  id: string;
  workspace: string;
  snapshot: string;
  reason: string;
  sourceJobId?: string;
  createdAt: string;
  status: string;
}

interface ProfileFormData {
  name: string;
  type: ProfileType;
  workspace: string;
  discordToken: string;
  channelId: string;
  model?: string;
  codexHome?: string;
  claudeConfigDir?: string;
  triggerMode?: string;
  allowDms?: boolean;
  operatorIds?: string;
  allowedUserIds?: string;
  allowedBotIds?: string;
  allowedChannelAuthorIds?: string;
  channelNoMentionAuthorIds?: string;
  channelHistoryLimit?: string;
  startupDmUserIds?: string;
  startupDmText?: string;
  startupChannelText?: string;
}

interface ChatMessageRecord {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  channelId?: string;
  senderName?: string;
  timestamp?: string;
}

interface ProfileSettingsData {
  type: ProfileType;
  workspace: string;
  discordToken?: string;
  botName: string;
  model: string;
  codexHome?: string;
  claudeConfigDir?: string;
  triggerMode: string;
  allowDms: boolean;
  channelId: string;
  operatorIds?: string;
  allowedUserIds: string;
  allowedBotIds?: string;
  allowedChannelAuthorIds?: string;
  channelNoMentionAuthorIds?: string;
  channelHistoryLimit?: string;
  startupDmUserIds?: string;
  startupDmText?: string;
  startupChannelText?: string;
}

declare global {
  interface Window {
    cladexDesktop?: {
      chooseDirectory: () => Promise<string>;
    };
  }
}

const ACCESS_TOKEN_STORAGE_KEY = 'cladex-remote-access-token';
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1', '[::1]']);

function hasCsvValue(value?: string): boolean {
  return String(value || '')
    .split(',')
    .some((item) => item.trim().length > 0);
}

function profileCreateAccessError(type: ProfileType, channelId: string, allowDms: boolean, operatorIds: string, allowedUserIds: string): string {
  const hasChannel = hasCsvValue(channelId);
  const hasApprovedUser = hasCsvValue(operatorIds) || hasCsvValue(allowedUserIds);
  if (allowDms && !hasApprovedUser) {
    return 'Direct messages require an approved user or operator ID.';
  }
  if (type === 'Codex' && !hasChannel && !allowDms) {
    return 'Codex needs an allowed channel unless direct messages are enabled for an approved user.';
  }
  if (type === 'Codex' && !hasChannel && allowDms && !hasApprovedUser) {
    return 'Codex direct-message relays need an approved user ID.';
  }
  if (type === 'Claude' && !hasChannel && !hasApprovedUser) {
    return 'Claude needs an allowed channel or an approved user/operator ID.';
  }
  return '';
}

function isTrustedApiOrigin(value: string): boolean {
  if (!value) return false;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return false;
    }
    return LOOPBACK_HOSTS.has(parsed.hostname);
  } catch {
    return false;
  }
}

function readFileModeApiBase(): string {
  if (typeof window === 'undefined') return '';
  const raw = new URLSearchParams(window.location.search).get('apiBase') || '';
  if (!raw) return '';
  return isTrustedApiOrigin(raw) ? raw : '';
}

const FILE_MODE_API_BASE = readFileModeApiBase();
const API_BASE = typeof window !== 'undefined'
  ? (window.location.protocol !== 'file:' ? `${window.location.origin}/api` : (FILE_MODE_API_BASE || 'http://127.0.0.1:3001/api'))
  : 'http://127.0.0.1:3001/api';
const API_REQUEST_TIMEOUT_MS = 900000;
const API_POLL_TIMEOUT_MS = 8000;
const CLADEX_LOGO = new URL('../assets/icon.png', import.meta.url).href;
const FIRST_RUN_REQUIREMENTS = [
  'Python 3.10+ installed and reachable from PATH.',
  'At least one AI CLI installed: `codex` for Codex relays and/or `claude` for Claude relays.',
  'A Discord bot token plus an allowed channel id or approved DM user/operator id.',
  'A local workspace folder for the relay to use.',
];
const FIRST_RUN_STEPS = [
  'Open Add Relay.',
  'Choose Claude or Codex.',
  'Pick the workspace folder and paste the Discord bot token.',
  'Set the allowed Discord channel id or scoped DM allowlist, then save the profile.',
  'Start the relay and confirm it reaches Ready.',
];

class RemoteAccessTokenError extends Error {
  constructor(message = 'CLADEX remote access token required.') {
    super(message);
    this.name = 'RemoteAccessTokenError';
  }
}

class ApiRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
  }
}

class ApiRequestTimeoutError extends Error {
  constructor(message = 'CLADEX API request timed out.') {
    super(message);
    this.name = 'ApiRequestTimeoutError';
  }
}

type ApiFetchInit = RequestInit & { timeoutMs?: number; accessToken?: string };

function getStoredAccessToken(): string {
  try {
    return window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function storeAccessToken(token: string) {
  try {
    if (token.trim()) {
      window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, token.trim());
    } else {
      window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    }
  } catch {}
}

function maskSecret(value: string): string {
  const text = value.trim();
  if (!text) return '';
  if (text.length <= 8) return '********';
  return `${text.slice(0, 4)}****${text.slice(-4)}`;
}

async function chooseWorkspaceFolder(currentValue = ''): Promise<string> {
  try {
    const chosen = await window.cladexDesktop?.chooseDirectory?.();
    return chosen || currentValue;
  } catch {
    return currentValue;
  }
}

function looksTechnicalLabel(value: string | undefined): boolean {
  const normalized = (value || '').trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  if (/^[a-z0-9]+-[0-9a-f]{6,}$/.test(normalized)) {
    return true;
  }
  if (normalized === 'codexcmd' || normalized === 'claudecmd' || normalized === 'relay' || normalized === 'bot') {
    return true;
  }
  return false;
}

function labelFor(profile: Profile): string {
  if (profile.displayName && !looksTechnicalLabel(profile.displayName)) {
    return profile.displayName;
  }
  if (profile.botName && !looksTechnicalLabel(profile.botName)) {
    return profile.botName;
  }
  return humanize(profile.workspaceLabel || profile.technicalName || profile.name || 'Relay');
}

function workspaceFor(profile: Profile): string {
  return profile.workspaceLabel || profile.workspace.split(/[\\/]/).filter(Boolean).pop() || profile.workspace;
}

function profileKey(profile: Profile): string {
  return `${profile.relayType}:${profile.id}`;
}

function channelFor(profile: Profile): string {
  return profile.channelLabel || (profile.activeChannel ? `Channel ${profile.activeChannel}` : profile.discordChannel ? `Channel ${profile.discordChannel}` : 'Unassigned');
}

function accountHomeFor(profile: Profile): string {
  if (profile.type === 'Codex') {
    return profile.codexHome || 'Default Codex account';
  }
  return profile.claudeConfigDir || 'Default Claude account';
}

function relayCardNote(profile: Profile): string {
  if (profile.statusText) {
    return profile.statusText;
  }
  if (profile.running && !profile.ready) {
    return 'Starting and waiting for the provider handshake.';
  }
  if (profile.running && profile.ready) {
    return 'Ready for the next Discord turn.';
  }
  return 'Relay is offline until you start it.';
}

function humanize(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .trim()
    .split(/\s+/)
    .slice(0, 4)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ') || 'Relay';
}

function fetchTargetIsTrusted(url: string): boolean {
  if (typeof window === 'undefined') return true;
  if (url.startsWith('/')) return true;
  try {
    const parsed = new URL(url, window.location.origin);
    if (window.location.protocol !== 'file:' && parsed.origin === window.location.origin) {
      return true;
    }
    return isTrustedApiOrigin(parsed.origin);
  } catch {
    return false;
  }
}

async function fetchJson<T>(url: string, init?: ApiFetchInit): Promise<T> {
  const {
    timeoutMs = API_REQUEST_TIMEOUT_MS,
    accessToken: accessTokenOverride,
    signal: callerSignal,
    headers: initHeaders,
    ...fetchInit
  } = init || {};
  const headers = new Headers(initHeaders || {});
  const accessToken = accessTokenOverride ?? getStoredAccessToken();
  if (accessToken && fetchTargetIsTrusted(url)) {
    headers.set('X-CLADEX-Access-Token', accessToken);
  }
  const controller = new AbortController();
  let timedOut = false;
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const abortFromCaller = () => controller.abort();
  if (callerSignal) {
    if (callerSignal.aborted) {
      controller.abort();
    } else {
      callerSignal.addEventListener('abort', abortFromCaller, { once: true });
    }
  }
  if (timeoutMs > 0) {
    timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }
  try {
    const response = await fetch(url, { ...fetchInit, headers, signal: controller.signal });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      if (response.status === 401 && payload?.authRequired) {
        throw new RemoteAccessTokenError(payload?.error || 'CLADEX remote access token required.');
      }
      throw new ApiRequestError(payload?.error || 'Request failed', response.status);
    }
    return response.json();
  } catch (error) {
    if (timedOut) {
      throw new ApiRequestTimeoutError(`CLADEX API request timed out after ${Math.round(timeoutMs / 1000)}s.`);
    }
    throw error;
  } finally {
    if (timeoutId !== undefined) {
      clearTimeout(timeoutId);
    }
    callerSignal?.removeEventListener?.('abort', abortFromCaller);
  }
}

async function fetchOptionalJson<T>(url: string, fallback: T, init?: ApiFetchInit): Promise<T> {
  try {
    return await fetchJson<T>(url, init);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) {
      return fallback;
    }
    throw error;
  }
}

const api = {
  profiles: () => fetchJson<Profile[]>(`${API_BASE}/profiles`, { timeoutMs: API_POLL_TIMEOUT_MS }),
  projects: () => fetchJson<ProjectRecord[]>(`${API_BASE}/projects`, { timeoutMs: API_POLL_TIMEOUT_MS }),
  runtimeInfo: () => fetchJson<RuntimeInfo>(`${API_BASE}/runtime-info`, { timeoutMs: API_POLL_TIMEOUT_MS }),
  logs: (id: string, relayType: RelayType) => fetchJson<{ logs: string[] }>(`${API_BASE}/profiles/${id}/logs?type=${relayType}`, { timeoutMs: API_POLL_TIMEOUT_MS }),
  chatHistory: (id: string, relayType: RelayType) => fetchJson<{ messages: ChatMessageRecord[] }>(`${API_BASE}/profiles/${id}/chat/history?type=${relayType}`, { timeoutMs: API_POLL_TIMEOUT_MS }),
  sendChat: (id: string, relayType: RelayType, body: { message: string; channelId?: string; senderName?: string; senderId?: string }) =>
    fetchJson<{ ok: boolean; reply: string; channelId?: string }>(`${API_BASE}/profiles/${id}/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, type: relayType }) }),
  createProfile: (body: ProfileFormData) => fetchJson(`${API_BASE}/profiles`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  updateProfile: (id: string, relayType: RelayType, body: ProfileSettingsData) =>
    fetchJson(`${API_BASE}/profiles/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, type: relayType }) }),
  startRelay: (id: string, relayType: RelayType) => fetchJson(`${API_BASE}/profiles/${id}/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: relayType }) }),
  stopRelay: (id: string, relayType: RelayType) => fetchJson(`${API_BASE}/profiles/${id}/stop`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: relayType }) }),
  restartRelay: (id: string, relayType: RelayType) => fetchJson(`${API_BASE}/profiles/${id}/restart`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: relayType }) }),
  deleteProfile: (id: string, relayType: RelayType) => fetchJson(`${API_BASE}/profiles/${id}?type=${relayType}`, { method: 'DELETE' }),
  stopAll: () => fetchJson(`${API_BASE}/actions/stop-all`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) }),
  createProject: (name: string, members: Array<{ id: string; relayType: RelayType }>) =>
    fetchJson(`${API_BASE}/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, members }) }),
  startProject: (name: string) => fetchJson(`${API_BASE}/projects/${encodeURIComponent(name)}/start`, { method: 'POST' }),
  stopProject: (name: string) => fetchJson(`${API_BASE}/projects/${encodeURIComponent(name)}/stop`, { method: 'POST' }),
  removeProject: (name: string) => fetchJson(`${API_BASE}/projects/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  reviews: () => fetchJson<ReviewJob[]>(`${API_BASE}/reviews`, { timeoutMs: API_POLL_TIMEOUT_MS }),
  fixRuns: () => fetchOptionalJson<FixRun[]>(`${API_BASE}/fix-runs`, [], { timeoutMs: API_POLL_TIMEOUT_MS }),
  fixRun: (id: string) => fetchJson<FixRun>(`${API_BASE}/fix-runs/${id}`, { timeoutMs: API_POLL_TIMEOUT_MS }),
  backups: () => fetchJson<BackupRecord[]>(`${API_BASE}/backups`, { timeoutMs: API_POLL_TIMEOUT_MS }),
  analyzeReview: (body: { workspace: string; provider: ReviewProvider; allowSelfReview?: boolean }) =>
    fetchJson<ReviewAnalysis>(`${API_BASE}/reviews/analyze`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  startReview: (body: { workspace: string; provider: ReviewProvider; agents: number; title?: string; accountHome?: string; allowSelfReview?: boolean; backupBeforeReview?: boolean }) =>
    fetchJson<ReviewJob>(`${API_BASE}/reviews`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  startFixReview: (id: string, body: { allowSelfFix?: boolean } = {}) =>
    fetchJson<FixRun>(`${API_BASE}/reviews/${id}/fix`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  cancelFixRun: (id: string) => fetchJson<FixRun>(`${API_BASE}/fix-runs/${id}/cancel`, { method: 'POST' }),
  createBackup: (body: { workspace: string; reason?: string }) =>
    fetchJson<BackupRecord>(`${API_BASE}/backups`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  createFixPlan: (id: string) => fetchJson<ReviewJob>(`${API_BASE}/reviews/${id}/fix-plan`, { method: 'POST' }),
  cancelReview: (id: string) => fetchJson<ReviewJob>(`${API_BASE}/reviews/${id}/cancel`, { method: 'POST' }),
  reviewFindings: (id: string) => fetchJson<{ jobId: string; findings: ReviewFinding[] }>(`${API_BASE}/reviews/${id}/findings`),
  listDirectories: (currentPath = '') => fetchJson<DirectoryListResponse>(`${API_BASE}/fs/list${currentPath ? `?path=${encodeURIComponent(currentPath)}` : ''}`),
};

type RefreshFailure = { label: string; error: unknown };

function refreshErrorText(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
}

export function refreshStatusMessage(failures: RefreshFailure[], prefix = 'Partial refresh'): string {
  if (!failures.length) {
    return '';
  }
  const shown = failures.slice(0, 3).map((failure) => `${failure.label}: ${refreshErrorText(failure.error)}`);
  const remaining = failures.length - shown.length;
  return `${prefix}: ${shown.join('; ')}${remaining > 0 ? `; ${remaining} more` : ''}`;
}

function recordRefreshResult<T>(
  result: PromiseSettledResult<T>,
  label: string,
  apply: (value: T) => void,
  failures: RefreshFailure[],
): boolean {
  if (result.status === 'fulfilled') {
    apply(result.value);
    return true;
  }
  failures.push({ label, error: result.reason });
  return false;
}

export default function App() {
  const [view, setView] = useState<ViewName>('relays');
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [reviewJobs, setReviewJobs] = useState<ReviewJob[]>([]);
  const [fixRuns, setFixRuns] = useState<FixRun[]>([]);
  const [backups, setBackups] = useState<BackupRecord[]>([]);
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeInfo | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [activeModal, setActiveModal] = useState<'add' | 'edit' | 'logs' | 'settings' | 'workgroup' | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [errorText, setErrorText] = useState('');
  const [bootPending, setBootPending] = useState(true);
  const [remoteAuthRequired, setRemoteAuthRequired] = useState(false);
  const [remoteAccessTokenDraft, setRemoteAccessTokenDraft] = useState(() => getStoredAccessToken());
  const [allowCladexSelfReview, setAllowCladexSelfReview] = useState(false);
  const lastActionErrorRef = useRef('');
  const bootFailureCount = useRef(0);
  const loadAllInFlight = useRef(false);
  // Dark mode is hardcoded (no toggle ships in v3). The constant is read
  // by ~6 dock-style helpers below to pick the right surface tones.
  const isDark = true;

  // Simple ↔ Advanced view mode. Persists across launches via localStorage.
  // Simple = essentials only (the default for new operators). Advanced =
  // every surface exposes its full set of knobs and inspector rows.
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    if (typeof window === 'undefined') return 'simple';
    try {
      const saved = window.localStorage.getItem('cladex.viewMode');
      return saved === 'advanced' ? 'advanced' : 'simple';
    } catch {
      return 'simple';
    }
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem('cladex.viewMode', viewMode);
    } catch {
      /* ignore quota / privacy-mode failures */
    }
  }, [viewMode]);

  // One-shot mount effect — adds `.dark` to <html> for Tailwind's dark
  // variants. Empty deps list because there is no toggle to track.
  useEffect(() => {
    document.documentElement.classList.add('dark');
  }, []);

  const loadAll = useCallback(async (silent = false) => {
    // Skip overlapping silent refreshes so a slow Promise.all (e.g. backend
    // bootstrap on first run) doesn't queue up a backlog of polls.
    if (silent && loadAllInFlight.current) {
      return;
    }
    loadAllInFlight.current = true;
    let keepLoading = false;
    if (!silent) {
      setLoading(true);
    }
    try {
      const failures: RefreshFailure[] = [];
      let appliedAny = false;
      let fixRunRows: FixRun[] | null = null;
      const [profileRows, projectRows, runtime, reviews, fixRunsResult, backupRows] = await Promise.allSettled([
        api.profiles(),
        api.projects(),
        api.runtimeInfo(),
        api.reviews(),
        api.fixRuns(),
        api.backups(),
      ]);
      appliedAny = recordRefreshResult(profileRows, 'profiles', setProfiles, failures) || appliedAny;
      appliedAny = recordRefreshResult(projectRows, 'workgroups', setProjects, failures) || appliedAny;
      appliedAny = recordRefreshResult(runtime, 'runtime', setRuntimeInfo, failures) || appliedAny;
      appliedAny = recordRefreshResult(reviews, 'reviews', setReviewJobs, failures) || appliedAny;
      if (fixRunsResult.status === 'fulfilled') {
        fixRunRows = fixRunsResult.value;
        appliedAny = true;
      } else {
        failures.push({ label: 'fix runs', error: fixRunsResult.reason });
      }
      appliedAny = recordRefreshResult(backupRows, 'backups', setBackups, failures) || appliedAny;

      if (fixRunRows !== null) {
        const baseFixRuns = fixRunRows;
        const detailResults = await Promise.allSettled(
          baseFixRuns.map((run) => (isInFlightStatus(run.status) ? api.fixRun(run.id) : Promise.resolve(run)))
        );
        const detailedFixRuns = detailResults.map((result, index) => {
          if (result.status === 'fulfilled') {
            return result.value;
          }
          failures.push({ label: `fix run ${baseFixRuns[index]?.id || index + 1}`, error: result.reason });
          return baseFixRuns[index];
        });
        setFixRuns(detailedFixRuns);
      }

      if (failures.some((failure) => failure.error instanceof RemoteAccessTokenError)) {
        setRemoteAuthRequired(true);
        setBootPending(false);
        setErrorText('');
        return;
      }

      if (!appliedAny) {
        const message = refreshStatusMessage(failures, 'Refresh failed') || 'Failed to refresh CLADEX state.';
        const nextFailures = bootFailureCount.current + 1;
        bootFailureCount.current = nextFailures;
        if (bootPending && nextFailures < 5) {
          keepLoading = true;
          setErrorText('');
        } else {
          setBootPending(false);
          setErrorText(message);
        }
        return;
      }

      setRemoteAuthRequired(false);
      bootFailureCount.current = 0;
      setBootPending(false);
      setErrorText(refreshStatusMessage(failures));
    } catch (error) {
      if (error instanceof RemoteAccessTokenError) {
        setRemoteAuthRequired(true);
        setBootPending(false);
        setErrorText('');
        return;
      }
      const message = error instanceof Error ? error.message : 'Failed to refresh CLADEX state.';
      const nextFailures = bootFailureCount.current + 1;
      bootFailureCount.current = nextFailures;
      if (bootPending && nextFailures < 5) {
        keepLoading = true;
        setErrorText('');
      } else {
        setBootPending(false);
        setErrorText(message);
      }
    } finally {
      loadAllInFlight.current = false;
      if (!silent) {
        setLoading(keepLoading ? true : false);
      }
    }
  }, [bootPending]);

  useEffect(() => {
    void loadAll();
    const interval = window.setInterval(() => void loadAll(true), 5000);
    return () => window.clearInterval(interval);
  }, [loadAll]);

  useEffect(() => {
    if (!profiles.length) {
      setSelectedProfileId(null);
      return;
    }
    if (!selectedProfileId || !profiles.some((profile) => profileKey(profile) === selectedProfileId)) {
      setSelectedProfileId(profileKey(profiles[0]));
    }
  }, [profiles, selectedProfileId]);

  const selectedProfile = profiles.find((profile) => profileKey(profile) === selectedProfileId) || null;

  async function runAction(key: string, action: () => Promise<unknown>): Promise<boolean> {
    setBusyKey(key);
    try {
      await action();
      await loadAll(true);
      setErrorText('');
      lastActionErrorRef.current = '';
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Action failed.';
      lastActionErrorRef.current = message;
      setErrorText(message);
      return false;
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className={`relative min-h-screen overflow-hidden font-sans transition-colors duration-500 selection:bg-indigo-500/30 ${isDark ? 'bg-[#050505] text-gray-100' : 'bg-[#f2efe7] text-slate-900'}`}>
      <CladexBackground isDark={isDark} />
      <div className={`pointer-events-none absolute inset-0 z-0 transition-opacity duration-500 ${isDark ? 'bg-[radial-gradient(circle_at_top,rgba(249,115,22,0.12),transparent_28%),radial-gradient(circle_at_bottom_right,rgba(16,185,129,0.12),transparent_32%)] opacity-100' : 'bg-[radial-gradient(circle_at_top,rgba(212,115,94,0.16),transparent_30%),radial-gradient(circle_at_bottom_right,rgba(125,181,165,0.18),transparent_34%)] opacity-80'}`} />
      <main className="relative z-10 flex min-h-screen flex-col overflow-y-auto pb-24 sm:pb-28">
        <header className="mx-auto flex w-full max-w-[1640px] flex-col gap-4 px-4 pb-2 pt-5 sm:px-8 sm:pt-7 lg:flex-row lg:items-start lg:justify-between lg:gap-6">
          <div className="flex items-center gap-4">
            <div className={`relative h-12 w-12 overflow-hidden rounded-[18px] border shadow-[0_0_28px_rgba(99,102,241,0.16)] ${isDark ? 'border-white/10 bg-white/5' : 'border-black/10 bg-white/70 shadow-[0_0_30px_rgba(212,115,94,0.12)]'}`}>
              <img src={CLADEX_LOGO} alt="CLADEX" className="h-full w-full object-cover" />
            </div>
            <div>
              <h1 className={`text-[1.9rem] leading-none font-black tracking-tight sm:text-[2.15rem] ${isDark ? 'text-white' : 'text-slate-900'}`}>ClaDex</h1>
              <p className={`mt-1.5 font-mono text-[11px] uppercase tracking-[0.32em] ${isDark ? 'text-orange-300/85' : 'text-[#b15f4e]'}`}>Unified Relay Network</p>
            </div>
          </div>
          <div className="flex items-center gap-2 self-start lg:pt-4">
            <ViewModeToggle value={viewMode} onChange={setViewMode} />
            <MiniIconButton label="Refresh" icon={<RefreshCw size={15} />} onClick={() => void loadAll()} />
            <MiniIconButton label="Stop All" icon={<PauseCircle size={15} />} tone="danger" onClick={() => void runAction('stop-all', api.stopAll)} />
          </div>
        </header>

        {!bootPending && errorText ? <div className={`mx-auto mt-3 w-full max-w-[1640px] rounded-2xl border px-4 py-3 text-sm ${isDark ? 'border-amber-500/20 bg-amber-500/10 text-amber-100' : 'border-amber-300 bg-amber-50 text-amber-950'}`}>{errorText}</div> : null}

        <AnimatePresence mode="wait">
          {view === 'relays' ? (
            <motion.div key="relays" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
              <RelayDashboard
                profiles={profiles}
                runtimeInfo={runtimeInfo}
                loading={loading}
                bootPending={bootPending}
                busyKey={busyKey}
                errorText={errorText}
                onRefresh={() => void loadAll()}
                onStart={(profile) => void runAction(`start-${profileKey(profile)}`, () => api.startRelay(profile.id, profile.relayType))}
                onStop={(profile) => void runAction(`stop-${profileKey(profile)}`, () => api.stopRelay(profile.id, profile.relayType))}
                onRestart={(profile) => void runAction(`restart-${profileKey(profile)}`, () => api.restartRelay(profile.id, profile.relayType))}
                onDelete={(profile) => void runAction(`delete-${profileKey(profile)}`, () => api.deleteProfile(profile.id, profile.relayType))}
                onEdit={(profile) => {
                  setSelectedProfileId(profileKey(profile));
                  setActiveModal('edit');
                }}
                onLogs={(profile) => {
                  setSelectedProfileId(profileKey(profile));
                  setActiveModal('logs');
                }}
              />
            </motion.div>
          ) : view === 'workgroups' ? (
            <motion.div key="workgroups" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
              <WorkgroupsView
                projects={projects}
                profiles={profiles}
                busyKey={busyKey}
                onCreate={() => setActiveModal('workgroup')}
                onStart={(name) => void runAction(`project-start-${name}`, () => api.startProject(name))}
                onStop={(name) => void runAction(`project-stop-${name}`, () => api.stopProject(name))}
                onRemove={(name) => void runAction(`project-remove-${name}`, () => api.removeProject(name))}
              />
            </motion.div>
          ) : view === 'review' ? (
            <motion.div key="review" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
              <ReviewProjectView
                jobs={reviewJobs}
                fixRuns={fixRuns}
                backups={backups}
                busyKey={busyKey}
                allowCladexSelfReview={allowCladexSelfReview}
                viewMode={viewMode}
                onAnalyze={(body) => api.analyzeReview(body)}
                onStart={(body) => void runAction('review-start', () => api.startReview(body))}
                onFixPlan={(job) => void runAction(`review-fix-${job.id}`, () => api.createFixPlan(job.id))}
                onFixReview={(job, options) => void runAction(`review-fix-run-${job.id}`, () => api.startFixReview(job.id, options))}
                onCancel={(job) => void runAction(`review-cancel-${job.id}`, () => api.cancelReview(job.id))}
                onCancelFixRun={(run) => void runAction(`fix-run-cancel-${run.id}`, () => api.cancelFixRun(run.id))}
                onCreateBackup={(workspace) => void runAction('backup-create', () => api.createBackup({ workspace, reason: 'manual' }))}
              />
            </motion.div>
          ) : (
            <motion.div key="live" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
              <LiveFeed profiles={profiles} selectedProfileId={selectedProfileId} onSelectProfile={setSelectedProfileId} viewMode={viewMode} />
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      <div className="fixed inset-x-3 bottom-3 z-50 sm:inset-x-auto sm:bottom-6 sm:left-1/2 sm:-translate-x-1/2">
        <div className={`flex items-center justify-between gap-1 overflow-x-auto rounded-2xl border p-2 backdrop-blur-xl shadow-2xl transition-colors duration-500 sm:gap-2 ${isDark ? 'border-white/10 bg-white/5 shadow-black/50' : 'border-slate-300/70 bg-white/80 shadow-slate-300/50'}`}>
          <DockButton icon={<LayoutGrid />} label="Relays" active={view === 'relays'} onClick={() => setView('relays')} light={!isDark} />
          <DockButton icon={<FolderKanban />} label="Workgroups" active={view === 'workgroups'} onClick={() => setView('workgroups')} light={!isDark} />
          <DockButton icon={<SearchCheck />} label="Review Swarm" active={view === 'review'} onClick={() => setView('review')} light={!isDark} />
          <DockButton icon={<MessageSquare />} label="Live Console" active={view === 'live'} onClick={() => setView('live')} light={!isDark} />
          <div className={`mx-2 h-8 w-px ${isDark ? 'bg-white/10' : 'bg-slate-300/80'}`} />
          <DockButton icon={<Plus />} label="Add Relay" onClick={() => setActiveModal('add')} light={!isDark} />
          <DockButton icon={<Settings />} label="Runtime" onClick={() => setActiveModal('settings')} light={!isDark} />
        </div>
      </div>

      <AnimatePresence>
        {remoteAuthRequired ? (
          <RemoteAccessModal
            token={remoteAccessTokenDraft}
            onChangeToken={setRemoteAccessTokenDraft}
            onSubmit={async () => {
              const token = remoteAccessTokenDraft.trim();
              await fetchJson<RuntimeInfo>(`${API_BASE}/runtime-info`, {
                timeoutMs: API_POLL_TIMEOUT_MS,
                accessToken: token,
              });
              storeAccessToken(token);
              await loadAll();
            }}
          />
        ) : null}
        {activeModal === 'add' ? <AddProfileModal viewMode={viewMode} onClose={() => setActiveModal(null)} onSubmit={async (data) => { if (await runAction('create-profile', () => api.createProfile(data))) { setActiveModal(null); return; } throw new Error(lastActionErrorRef.current || 'Failed to save relay.'); }} /> : null}
        {activeModal === 'edit' && selectedProfile ? <EditProfileModal profile={selectedProfile} viewMode={viewMode} onClose={() => setActiveModal(null)} onSubmit={async (data) => { if (await runAction(`update-${selectedProfile.id}`, () => api.updateProfile(selectedProfile.id, selectedProfile.relayType, data))) { setActiveModal(null); return; } throw new Error(lastActionErrorRef.current || 'Failed to save relay.'); }} /> : null}
        {activeModal === 'logs' && selectedProfile ? <LogsModal profile={selectedProfile} onClose={() => setActiveModal(null)} /> : null}
        {activeModal === 'settings' ? (
          <SettingsModal
            runtimeInfo={runtimeInfo}
            allowCladexSelfReview={allowCladexSelfReview}
            onChangeAllowCladexSelfReview={setAllowCladexSelfReview}
            onClose={() => setActiveModal(null)}
            onStopAll={() => void runAction('stop-all', api.stopAll)}
          />
        ) : null}
        {activeModal === 'workgroup' ? <WorkgroupModal profiles={profiles} onClose={() => setActiveModal(null)} onSubmit={async (name, members) => { if (await runAction(`workgroup-${name}`, () => api.createProject(name, members))) { setActiveModal(null); return; } throw new Error(lastActionErrorRef.current || 'Failed to save workgroup.'); }} /> : null}
      </AnimatePresence>
    </div>
  );
}

function RelayDashboard({
  profiles,
  runtimeInfo,
  loading,
  bootPending,
  busyKey,
  errorText,
  onRefresh,
  onStart,
  onStop,
  onRestart,
  onDelete,
  onEdit,
  onLogs,
}: {
  profiles: Profile[];
  runtimeInfo: RuntimeInfo | null;
  loading: boolean;
  bootPending: boolean;
  busyKey: string | null;
  errorText: string;
  onRefresh: () => void;
  onStart: (profile: Profile) => void;
  onStop: (profile: Profile) => void;
  onRestart: (profile: Profile) => void;
  onDelete: (profile: Profile) => void;
  onEdit: (profile: Profile) => void;
  onLogs: (profile: Profile) => void;
}) {
  return (
    <div className="mx-auto flex w-full max-w-[1640px] flex-1 flex-col px-4 pb-10 pt-4 sm:px-8">
      {loading ? (
        <EmptyState
          title={bootPending ? 'Starting the local CLADEX runtime...' : 'Loading relay state...'}
          detail={bootPending ? 'Waiting for the packaged relay API to become ready.' : 'Refreshing current relay state and active workspaces.'}
          compact={false}
        />
      ) : errorText && profiles.length === 0 ? (
        <div className="space-y-6">
          <EmptyState
            title="CLADEX could not reach the local relay runtime."
            detail="If this is a fresh portable install, make sure Python 3.10+ is installed, then restart CLADEX. You also still need the `codex` and/or `claude` CLI for the relay type you want to run."
            actionLabel="Refresh"
            onAction={onRefresh}
          />
          <FirstRunGuide packaged={runtimeInfo?.packaged ?? true} includeTroubleshooting />
        </div>
      ) : profiles.length === 0 ? (
        <div className="space-y-6">
          <EmptyState title="No relays configured yet." detail="Choose Add Relay and register a Claude or Codex workspace. The desktop app manages local relays, but it does not bundle Python or the Codex/Claude CLIs for you." />
          <FirstRunGuide packaged={runtimeInfo?.packaged ?? false} />
        </div>
      ) : (
        <div className="grid auto-rows-fr gap-4 sm:gap-6 md:grid-cols-2 xl:grid-cols-3">
          {profiles.map((profile) => (
            <React.Fragment key={profileKey(profile)}>
              <RelayCard
                profile={profile}
                busy={Boolean(busyKey?.includes(profileKey(profile)))}
                onStart={() => onStart(profile)}
                onStop={() => onStop(profile)}
                onRestart={() => onRestart(profile)}
                onDelete={() => onDelete(profile)}
                onEdit={() => onEdit(profile)}
                onLogs={() => onLogs(profile)}
              />
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

function RelayCard({
  profile,
  busy,
  onStart,
  onStop,
  onRestart,
  onDelete,
  onEdit,
  onLogs,
}: {
  profile: Profile;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
  onRestart: () => void;
  onDelete: () => void;
  onEdit: () => void;
  onLogs: () => void;
}) {
  const isClaude = profile.type === 'Claude';
  const running = profile.running;
  const accent = isClaude ? '#d4735e' : '#7db5a5';
  // v3 ui-design pass: each relay is a "rack channel" on a dispatch console.
  // Hover should feel like moving a hand near the channel — the LED-strip
  // brightens, the chassis lifts a hair from the rack. NOT marketing-card
  // 3D-tilt + spotlight + scale. Removed: rotateX/rotateY tilt (felt jarring,
  // exposed corner clipping with the new truncate min-w-0); pointer-following
  // spotlight (decorative, not informational); whileHover scale (the chassis
  // doesn't get bigger — the indicator brightens).

  return (
    <motion.div
      className="group relative h-[262px] sm:h-[276px]"
    >
      <div className="absolute inset-0 rounded-[32px] bg-black/20 blur-2xl transition-opacity duration-300 group-hover:bg-black/35 dark:bg-black/30 dark:group-hover:bg-black/45" />
      <div className="relative h-full overflow-hidden rounded-[28px] border border-slate-200/70 bg-white/70 p-5 shadow-[0_18px_44px_rgba(15,23,42,0.12)] backdrop-blur-xl transition-[box-shadow,border-color,transform] duration-300 ease-out group-hover:-translate-y-[2px] group-hover:shadow-[0_22px_56px_rgba(15,23,42,0.18)] dark:border-white/10 dark:bg-[#09090b]/90 dark:shadow-2xl dark:group-hover:border-white/20">
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#0f172a08_1px,transparent_1px),linear-gradient(to_bottom,#0f172a08_1px,transparent_1px)] bg-[size:24px_24px] opacity-60 dark:bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)]" />
        {/* The channel's status LED strip — single accent strip on the right
            edge that brightens on hover. Replaces the pointer-tracking
            spotlight. The opacity step (18% → 42%) is what acknowledges the
            user, not a 3D tilt. */}
        <div
          className="pointer-events-none absolute inset-y-6 right-0 w-[3px] rounded-full opacity-[0.18] transition-opacity duration-300 group-hover:opacity-[0.42]"
          style={{ background: accent, boxShadow: `0 0 12px ${accent}40` }}
        />
        <div className="pointer-events-none absolute -right-14 top-8 h-24 w-24 rounded-full blur-3xl" style={{ background: `${accent}28` }} />
        <div className="relative z-10 flex h-full flex-col">
        <div className="flex items-start justify-between gap-3 sm:gap-4">
          {/* T3.4 / frontend-audit #4: min-w-0 + truncate so a long display
              name doesn't push the connector graphic and Stop/Start button
              out of the fixed-height card. title= surfaces the full name
              on hover. */}
          <div className="min-w-0 flex-1">
            <div className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.24em] ${isClaude ? 'border-orange-500/30 bg-orange-500/10 text-orange-700 dark:text-orange-200' : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200'}`}>{profile.type}</div>
            <h3 title={labelFor(profile)} className="mt-3 truncate text-[1.55rem] leading-none font-bold tracking-tight text-slate-900 sm:text-[1.9rem] dark:text-white">{labelFor(profile)}</h3>
            <p title={workspaceFor(profile)} className="mt-2 truncate text-sm text-slate-500 dark:text-gray-400"># {workspaceFor(profile)}</p>
          </div>
          <div className="flex shrink-0 gap-1.5 sm:gap-2">
            <MiniIconButton label="Logs" icon={<FileText size={14} />} onClick={onLogs} />
            <MiniIconButton label="Edit" icon={<Pencil size={14} />} onClick={onEdit} />
            <MiniIconButton label="Remove" icon={<Trash2 size={14} />} tone="danger" onClick={onDelete} />
          </div>
        </div>

        <div className="mt-4 flex flex-1 items-center justify-center">
          <div className="flex w-full max-w-[220px] items-center justify-between">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl border-2 bg-white shadow-lg dark:bg-[#09090b]" style={{ borderColor: running ? accent : 'rgba(148,163,184,0.35)', color: running ? accent : undefined }}>
              {isClaude ? <Bot size={18} /> : <Terminal size={18} />}
            </div>
            <div className="relative mx-4 h-[2px] flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10">
              {running ? (
                <motion.div
                  className="absolute inset-y-0 left-[-35%] w-1/2"
                  style={{ background: `linear-gradient(to right, transparent, ${accent}, transparent)` }}
                  animate={{ x: ['-10%', '220%'] }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: 'linear' }}
                />
              ) : null}
            </div>
            <div className={`flex h-11 w-11 items-center justify-center rounded-2xl border-2 ${running ? 'border-[#5865f2] bg-[#5865f2]/10 text-[#5865f2]' : 'border-slate-300 bg-slate-100 text-slate-400 dark:border-white/10 dark:bg-white/5 dark:text-gray-500'}`}>
              <Hash size={18} />
            </div>
          </div>
        </div>

        <div className="mt-5 flex items-end justify-between gap-4">
          <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-gray-400">
            <span className={`h-2.5 w-2.5 rounded-full ${running ? (isClaude ? 'bg-orange-400' : 'bg-emerald-400') : 'bg-slate-400 dark:bg-gray-600'} ${running ? 'animate-pulse' : ''}`} />
            <div>
              <div className="font-medium text-slate-700 dark:text-gray-200">{running ? (profile.state === 'working' ? 'Working...' : 'Listening') : 'Offline'}</div>
              <div className="mt-1 text-xs text-slate-500 dark:text-gray-500">{running ? relayCardNote(profile) : 'Stopped'}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={running ? onStop : onStart}
              disabled={busy}
              className={`inline-flex min-w-[108px] items-center justify-center gap-2 rounded-2xl border px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${
                running
                  ? 'border-red-500/30 bg-red-500/10 text-red-200 hover:bg-red-500/20'
                  : isClaude
                    ? 'border-orange-500/30 bg-orange-500/10 text-orange-700 hover:bg-orange-500/20 dark:text-orange-200'
                    : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/20 dark:text-emerald-200'
              }`}
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : running ? <Square size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
              {running ? 'Stop' : 'Start'}
            </button>
          </div>
        </div>
      </div>
      </div>
    </motion.div>
  );
}

function WorkgroupsView({
  projects,
  profiles,
  busyKey,
  onCreate,
  onStart,
  onStop,
  onRemove,
}: {
  projects: ProjectRecord[];
  profiles: Profile[];
  busyKey: string | null;
  onCreate: () => void;
  onStart: (name: string) => void;
  onStop: (name: string) => void;
  onRemove: (name: string) => void;
}) {
  return (
    <div className="mx-auto flex w-full max-w-[1640px] flex-1 flex-col px-4 pb-8 pt-6 sm:px-8 sm:pt-8">
      <div className="mb-6 flex flex-col gap-4 sm:mb-8 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.24em] text-slate-500 dark:text-gray-500">Saved workgroups</div>
          <h2 className="mt-2 text-3xl font-black tracking-tight text-slate-900 dark:text-white">Start or stop related relays together.</h2>
          <p className="mt-2 max-w-2xl text-sm text-slate-600 dark:text-gray-400">This replaces the old project strip with a real workgroup surface in the desktop app.</p>
        </div>
        <ActionButton label="New Workgroup" icon={<Plus size={16} />} onClick={onCreate} />
      </div>

      {projects.length === 0 ? (
        <EmptyState title="No workgroups saved yet." detail="Create a group from the relays you already have registered." />
      ) : (
        <div className="grid gap-5 lg:grid-cols-2">
          {projects.map((project) => (
            <div key={project.name} className="rounded-[30px] border border-slate-200/80 bg-white/80 p-6 shadow-[0_18px_45px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-2xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Workgroup</div>
                  <div className="mt-2 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{project.name}</div>
                  <div className="mt-2 text-sm text-slate-600 dark:text-gray-400">{project.memberCount} relay{project.memberCount === 1 ? '' : 's'}</div>
                </div>
                <div className="flex gap-2">
                  <ActionButton label="Start" icon={<Play size={14} />} busy={busyKey === `project-start-${project.name}`} onClick={() => onStart(project.name)} />
                  <ActionButton label="Stop" icon={<Square size={14} />} busy={busyKey === `project-stop-${project.name}`} onClick={() => onStop(project.name)} />
                  <ActionButton label="Remove" icon={<Trash2 size={14} />} busy={busyKey === `project-remove-${project.name}`} tone="danger" onClick={() => onRemove(project.name)} />
                </div>
              </div>
              <div className="mt-5 space-y-3">
                {project.members.map((member) => {
                  const profile = profiles.find((row) => row.id === member.id && row.relayType === member.relayType);
                  return (
                    <div key={`${member.relayType}:${member.id}`} className="flex items-center justify-between rounded-2xl border border-slate-200/80 bg-slate-50/80 px-4 py-3 dark:border-white/5 dark:bg-black/30">
                      <div>
                        <div className="text-sm font-semibold text-slate-900 dark:text-white">{member.displayName}</div>
                        <div className="text-xs text-slate-500 dark:text-gray-500">{profile ? workspaceFor(profile) : member.workspace}</div>
                      </div>
                      <div className={`rounded-full px-2 py-1 text-[10px] font-bold uppercase tracking-[0.22em] ${member.relayType === 'claude' ? 'bg-orange-500/10 text-orange-700 dark:text-orange-200' : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-200'}`}>
                        {member.relayType}
                      </div>
                    </div>
                  );
                })}
                {project.missingMembers.length ? <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">Missing: {project.missingMembers.map((member) => `${member.relayType}:${member.name}`).join(', ')}</div> : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ReviewProjectView({
  jobs,
  fixRuns,
  backups,
  busyKey,
  allowCladexSelfReview,
  viewMode = 'simple',
  onAnalyze,
  onStart,
  onFixPlan,
  onFixReview,
  onCancel,
  onCancelFixRun,
  onCreateBackup,
}: {
  jobs: ReviewJob[];
  fixRuns: FixRun[];
  backups: BackupRecord[];
  busyKey: string | null;
  allowCladexSelfReview: boolean;
  viewMode?: ViewMode;
  onAnalyze: (body: { workspace: string; provider: ReviewProvider; allowSelfReview?: boolean }) => Promise<ReviewAnalysis>;
  onStart: (body: { workspace: string; provider: ReviewProvider; agents: number; title?: string; accountHome?: string; allowSelfReview?: boolean; backupBeforeReview?: boolean }) => void;
  onFixPlan: (job: ReviewJob) => void;
  onFixReview: (job: ReviewJob, options?: { allowSelfFix?: boolean }) => void;
  onCancel: (job: ReviewJob) => void;
  onCancelFixRun: (run: FixRun) => void;
  onCreateBackup: (workspace: string) => void;
}) {
  const isAdvanced = viewMode === 'advanced';
  void isAdvanced; // currently consumed below in inline conditionals
  const [workspace, setWorkspace] = useState('');
  const [title, setTitle] = useState('');
  const [provider, setProvider] = useState<ReviewProvider>('codex');
  const [agents, setAgents] = useState(8);
  const [codexAccountHome, setCodexAccountHome] = useState('');
  const [claudeAccountHome, setClaudeAccountHome] = useState('');
  const [backupBeforeReview, setBackupBeforeReview] = useState(true);
  const [activityTab, setActivityTab] = useState<ReviewActivityTab>('active');
  const [analysis, setAnalysis] = useState<ReviewAnalysis | null>(null);
  const [analysisError, setAnalysisError] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const analysisRequestRef = useRef(0);
  const analysisInputRef = useRef({ workspace, provider, allowSelfReview: allowCladexSelfReview });
  const accountHome = provider === 'codex' ? codexAccountHome : claudeAccountHome;
  const setAccountHome = provider === 'codex' ? setCodexAccountHome : setClaudeAccountHome;
  const reviewBusy = busyKey === 'review-start';
  const backupBusy = busyKey === 'backup-create';
  const workspaceFilled = workspace.trim().length > 0;
  const activeReviewJobs = jobs.filter((job) => isInFlightStatus(job.status));
  const historicalReviewJobs = jobs.filter((job) => !isInFlightStatus(job.status));
  const activeJobs = activeReviewJobs.length;
  const activeFixRuns = fixRuns.filter((run) => run.status === 'queued' || run.status === 'running').length;

  useEffect(() => {
    analysisInputRef.current = { workspace, provider, allowSelfReview: allowCladexSelfReview };
  }, [workspace, provider, allowCladexSelfReview]);

  async function analyzeTarget(nextWorkspace = workspace, selectedProvider = provider) {
    const target = nextWorkspace.trim();
    if (!target || analyzing) return;
    const requestId = analysisRequestRef.current + 1;
    analysisRequestRef.current = requestId;
    const allowSelfReview = allowCladexSelfReview;
    setAnalyzing(true);
    setAnalysisError('');
    try {
      const result = await onAnalyze({ workspace: target, provider: selectedProvider, allowSelfReview });
      const latest = analysisInputRef.current;
      if (
        requestId !== analysisRequestRef.current ||
        latest.workspace.trim() !== target ||
        latest.provider !== selectedProvider ||
        latest.allowSelfReview !== allowSelfReview
      ) {
        return;
      }
      setAnalysis(result);
      setWorkspace(result.workspace || target);
      setProvider(result.recommendation.provider);
      setAgents(result.recommendation.agents);
      const previousTitle = analysis?.recommendation.title || '';
      setTitle((current) => (!current.trim() || current === previousTitle ? (result.recommendation.title || `${result.projectName || 'Project'} deep scan`) : current));
    } catch (error) {
      if (requestId !== analysisRequestRef.current) {
        return;
      }
      setAnalysis(null);
      setAnalysisError(error instanceof Error ? error.message : 'Project Scout failed.');
    } finally {
      if (requestId === analysisRequestRef.current) {
        setAnalyzing(false);
      }
    }
  }

  function selectProvider(nextProvider: ReviewProvider) {
    analysisRequestRef.current += 1;
    analysisInputRef.current = { ...analysisInputRef.current, provider: nextProvider };
    setProvider(nextProvider);
    setAnalysis((current) => current ? { ...current, recommendation: { ...current.recommendation, provider: nextProvider } } : current);
  }

  return (
    <div className="review-swarm-page mx-auto flex w-full max-w-[1640px] flex-1 flex-col px-4 pb-8 pt-5 sm:px-8 sm:pt-7">
      <div className="mb-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-[10px] font-bold uppercase tracking-[0.24em] text-emerald-700 dark:text-emerald-300">Review Swarm</div>
            {isAdvanced ? (
              <>
                <MetaPill label="read-only lanes" />
                <MetaPill label="scratch-copy reviews" />
              </>
            ) : null}
          </div>
          <h2 className="mt-2 text-2xl font-black tracking-tight text-slate-950 dark:text-white sm:text-3xl">{isAdvanced ? 'Swarm control room' : 'Code review'}</h2>
          <p className="mt-2 max-w-3xl select-none text-sm leading-relaxed text-slate-600 dark:text-gray-400">
            {isAdvanced
              ? 'Pick a project folder, let Project Scout size the run, then dispatch Codex or Claude reviewers into isolated copies. Live work stays here; finished scans move behind History.'
              : 'Pick a project folder and run a review. The AI reads the project in a sandbox copy and reports findings. Nothing in your project is modified.'}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:justify-end">
          <div className="select-none rounded-[14px] border border-slate-200/80 bg-white/70 px-3 py-2 text-right dark:border-white/10 dark:bg-white/[0.035]">
            <div className="font-mono text-lg font-semibold text-slate-950 dark:text-white">{activeJobs}</div>
            <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-gray-500">scans live</div>
          </div>
          <div className="select-none rounded-[14px] border border-slate-200/80 bg-white/70 px-3 py-2 text-right dark:border-white/10 dark:bg-white/[0.035]">
            <div className="font-mono text-lg font-semibold text-slate-950 dark:text-white">{activeFixRuns}</div>
            <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-gray-500">fix runs</div>
          </div>
        </div>
      </div>

      <div className="grid min-h-0 gap-5 xl:grid-cols-[390px_minmax(0,1fr)]">
        <aside className="swarm-control-rail min-w-0 rounded-[22px] border border-slate-200/80 bg-white/75 p-4 shadow-[0_18px_50px_rgba(15,23,42,0.07)] dark:border-white/10 dark:bg-[#070908]/80 dark:shadow-[0_24px_80px_rgba(0,0,0,0.28)]">
          <div className="space-y-4">
            <div>
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Target folder</div>
                  <div className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">Project under review</div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {analysis ? <MetaPill label={`${analysis.fileCount} files`} mono /> : null}
                  <button
                    type="button"
                    disabled={reviewBusy || backupBusy || analyzing || !workspaceFilled}
                    onClick={() => {
                      if (!workspaceFilled || reviewBusy || backupBusy || analyzing) return;
                      setActivityTab('active');
                      onStart({ workspace, provider, agents, title, accountHome, allowSelfReview: allowCladexSelfReview, backupBeforeReview });
                    }}
                    className="inline-flex min-h-9 items-center justify-center gap-2 rounded-[12px] border border-emerald-400/35 bg-emerald-400/12 px-3 py-2 text-xs font-black text-emerald-800 transition-colors hover:bg-emerald-400/20 disabled:cursor-not-allowed disabled:opacity-45 dark:text-emerald-100"
                  >
                    {reviewBusy ? <Loader2 size={14} className="animate-spin" /> : <SearchCheck size={14} />}
                    Scan
                  </button>
                </div>
              </div>
              <BrowseField
                label="Folder path"
                value={workspace}
                onChange={(value) => {
                  analysisRequestRef.current += 1;
                  analysisInputRef.current = { ...analysisInputRef.current, workspace: value };
                  setWorkspace(value);
                  setAnalysis(null);
                  setAnalysisError('');
                }}
                onPicked={(value) => void analyzeTarget(value)}
                placeholder="C:\\Projects\\target-repo"
                buttonLabel="Choose target folder"
              />
            </div>
            <ProjectScoutCard
              workspaceFilled={workspaceFilled}
              analysis={analysis}
              errorText={analysisError}
              busy={analyzing}
              onAnalyze={() => void analyzeTarget()}
            />

            {isAdvanced ? (
              <div className="border-t border-slate-200/80 pt-4 dark:border-white/10">
                <FormInput
                  label="Run title (optional)"
                  value={title}
                  onChange={setTitle}
                  placeholder="Production readiness pass"
                  helper="Shown in the History tab so you can find this run later. If you leave it blank, CLADEX uses '{project} deep scan'."
                />
              </div>
            ) : null}

            {isAdvanced ? (
              <div className="rounded-[18px] border border-emerald-400/25 bg-emerald-500/[0.055] px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-emerald-700 dark:text-emerald-200">Sandbox copy</div>
                    <div className="mt-1 text-sm text-slate-600 dark:text-gray-400">Reviewers work in CLADEX scratch copies. The selected project stays untouched during review.</div>
                  </div>
                  <MetaPill label="isolated" />
                </div>
              </div>
            ) : null}

            <section className="border-t border-slate-200/80 pt-4 dark:border-white/10">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">{isAdvanced ? 'Swarm lanes' : 'Reviewer'}</div>
                  <div className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">{isAdvanced ? 'Provider and lane count' : 'Which AI does the review?'}</div>
                </div>
                {isAdvanced ? <div className="font-mono text-lg font-semibold text-slate-900 dark:text-white">{agents}</div> : null}
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <TypeButton active={provider === 'codex'} label="Codex" icon={<Terminal size={18} />} onClick={() => selectProvider('codex')} tone="emerald" />
                <TypeButton active={provider === 'claude'} label="Claude Code" icon={<Bot size={18} />} onClick={() => selectProvider('claude')} tone="orange" />
              </div>
              {isAdvanced ? (
                <label className="mt-4 block">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <div>
                      <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Parallel lanes</div>
                      <div className="mt-1 text-[12px] text-slate-500 dark:text-gray-500">More lanes = faster, more parallel API calls. 8 is a safe default; 50 is the cap.</div>
                    </div>
                    <div className="font-mono text-sm text-slate-700 dark:text-gray-300">1-50</div>
                  </div>
                  <input
                    type="range"
                    min={1}
                    max={50}
                    value={agents}
                    onChange={(event) => setAgents(Number(event.target.value))}
                    className="w-full accent-emerald-500"
                  />
                </label>
              ) : null}
              {isAdvanced ? (
                <details className="mt-4 rounded-[16px] border border-slate-200/80 bg-white/55 px-4 py-3 dark:border-white/10 dark:bg-black/25">
                  <summary className="cursor-pointer text-sm font-semibold text-slate-700 dark:text-gray-300">Use a different AI account for this scan</summary>
                  <div className="mt-4 space-y-4">
                    <BrowseField
                      label={provider === 'codex' ? 'Codex login/config folder' : 'Claude login/config folder'}
                      value={accountHome}
                      onChange={setAccountHome}
                      placeholder={provider === 'codex' ? 'Optional CODEX_HOME for a separate Codex account' : 'Optional CLAUDE_CONFIG_DIR for a separate Claude account'}
                      helper="Folder containing the AI CLI login. Useful if you have a dedicated 'reviewer' account separate from your everyday account. Leave blank to share your default login."
                      buttonLabel="Choose account folder"
                      stacked
                    />
                  </div>
                </details>
              ) : null}
            </section>

            {isAdvanced ? (
              <div className="border-t border-slate-200/80 pt-4 dark:border-white/10">
                <ToggleRow
                  checked={backupBeforeReview}
                  onChange={setBackupBeforeReview}
                  label="Save a snapshot of the project before scanning"
                  helper="On by default. Snapshots let you roll back the project to its pre-scan state if a Fix Run later modifies files you didn't expect."
                />
              </div>
            ) : null}

            <div className="sticky bottom-4 z-20 -mx-1 space-y-3 rounded-[18px] border border-slate-200/80 bg-white/88 p-3 shadow-[0_18px_45px_rgba(15,23,42,0.14)] backdrop-blur-xl dark:border-white/10 dark:bg-[#070908]/92 dark:shadow-[0_18px_55px_rgba(0,0,0,0.45)]">
              <button
                type="button"
                disabled={reviewBusy || backupBusy || analyzing || !workspaceFilled}
                aria-busy={reviewBusy || undefined}
                onClick={() => {
                  if (!workspaceFilled || reviewBusy || backupBusy || analyzing) return;
                  setActivityTab('active');
                  onStart({ workspace, provider, agents, title, accountHome, allowSelfReview: allowCladexSelfReview, backupBeforeReview });
                }}
                className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-[18px] bg-emerald-400 px-4 py-3 text-sm font-black text-[#03130d] transition-colors hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:bg-emerald-400"
              >
                {reviewBusy ? <Loader2 size={16} className="animate-spin" /> : <SearchCheck size={16} />}
                {reviewBusy ? 'Dispatching...' : 'Scan Now'}
              </button>
              <SecondaryButton
                label={backupBusy ? 'Saving snapshot...' : 'Save snapshot only'}
                busy={backupBusy || reviewBusy || analyzing || !workspaceFilled}
                onClick={() => {
                  if (!workspaceFilled || reviewBusy || backupBusy || analyzing) return;
                  onCreateBackup(workspace);
                }}
              />
            </div>
          </div>
        </aside>

        <SwarmActivityPanel
          activeTab={activityTab}
          onTabChange={setActivityTab}
          activeJobs={activeReviewJobs}
          historyJobs={historicalReviewJobs}
          fixRuns={fixRuns}
          backups={backups}
          busyKey={busyKey}
          onFixPlan={onFixPlan}
          onFixReview={onFixReview}
          onCancel={onCancel}
          onCancelFixRun={onCancelFixRun}
        />
      </div>
    </div>
  );
}

function SwarmActivityPanel({
  activeTab,
  onTabChange,
  activeJobs,
  historyJobs,
  fixRuns,
  backups,
  busyKey,
  onFixPlan,
  onFixReview,
  onCancel,
  onCancelFixRun,
}: {
  activeTab: ReviewActivityTab;
  onTabChange: (tab: ReviewActivityTab) => void;
  activeJobs: ReviewJob[];
  historyJobs: ReviewJob[];
  fixRuns: FixRun[];
  backups: BackupRecord[];
  busyKey: string | null;
  onFixPlan: (job: ReviewJob) => void;
  onFixReview: (job: ReviewJob, options?: { allowSelfFix?: boolean }) => void;
  onCancel: (job: ReviewJob) => void;
  onCancelFixRun: (run: FixRun) => void;
}) {
  const [historyLimit, setHistoryLimit] = useState(8);
  const visibleHistoryJobs = historyJobs.slice(0, historyLimit);
  const remainingHistoryJobs = Math.max(0, historyJobs.length - visibleHistoryJobs.length);
  const tabs: Array<{ id: ReviewActivityTab; label: string; count: number; icon: React.ReactNode }> = [
    { id: 'active', label: 'Active scans', count: activeJobs.length, icon: <Activity size={15} /> },
    { id: 'history', label: 'History', count: historyJobs.length, icon: <FileText size={15} /> },
    { id: 'fixes', label: 'Fix runs', count: fixRuns.length, icon: <Wrench size={15} /> },
    { id: 'snapshots', label: 'Snapshots', count: backups.length, icon: <FolderKanban size={15} /> },
  ];
  const panelTitle = activeTab === 'active'
    ? (activeJobs.length ? `${activeJobs.length} scan${activeJobs.length === 1 ? '' : 's'} in motion` : 'Standby deck')
    : activeTab === 'history'
      ? 'Scan history'
      : activeTab === 'fixes'
        ? 'Fix run queue'
        : 'Source snapshots';

  const renderReviewJob = (job: ReviewJob) => (
    <ReviewJobCard
      key={job.id}
      job={job}
      activeFixRun={fixRuns.find((run) => (run.reviewId || run.reviewJobId) === job.id && isInFlightStatus(run.status))}
      fixPlanBusy={busyKey === `review-fix-${job.id}`}
      fixReviewBusy={busyKey === `review-fix-run-${job.id}`}
      cancelBusy={busyKey === `review-cancel-${job.id}`}
      onFixPlan={() => onFixPlan(job)}
      onFixReview={(options) => onFixReview(job, options)}
      onCancel={() => onCancel(job)}
    />
  );

  return (
    <section className="swarm-activity-shell min-w-0 overflow-hidden rounded-[24px] border border-slate-200/80 bg-white/70 p-4 shadow-[0_18px_55px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-[#060706]/80 dark:shadow-[0_28px_90px_rgba(0,0,0,0.35)] sm:p-5">
      <div className="flex flex-col gap-4 border-b border-slate-200/80 pb-4 dark:border-white/10 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Swarm activity</div>
          <div className="mt-1 text-xl font-black tracking-tight text-slate-900 dark:text-white">{panelTitle}</div>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:justify-end">
          {tabs.map((tab) => (
            <ActivityTabButton
              key={tab.id}
              active={activeTab === tab.id}
              icon={tab.icon}
              label={tab.label}
              count={tab.count}
              onClick={() => onTabChange(tab.id)}
            />
          ))}
        </div>
      </div>

      <div className="mt-5">
        {activeTab === 'active' ? (
          activeJobs.length ? (
            <div className="space-y-4">{activeJobs.map(renderReviewJob)}</div>
          ) : (
            <HiveStandby historyCount={historyJobs.length} />
          )
        ) : null}

        {activeTab === 'history' ? (
          historyJobs.length ? (
            <div className="space-y-4">
              {visibleHistoryJobs.map(renderReviewJob)}
              {remainingHistoryJobs > 0 ? (
                <button
                  type="button"
                  onClick={() => setHistoryLimit((current) => current + 8)}
                  className="w-full rounded-2xl border border-dashed border-slate-200/80 bg-white/50 px-4 py-3 text-sm font-semibold text-slate-600 transition-colors hover:bg-white dark:border-white/10 dark:bg-black/20 dark:text-gray-300 dark:hover:bg-white/[0.06]"
                >
                  Show {Math.min(8, remainingHistoryJobs)} more scan{Math.min(8, remainingHistoryJobs) === 1 ? '' : 's'} ({remainingHistoryJobs} remaining)
                </button>
              ) : null}
            </div>
          ) : (
            <EmptyState title="No scan history yet." detail="Finished review swarms will be stored here after the first run." />
          )
        ) : null}

        {activeTab === 'fixes' ? (
          fixRuns.length ? (
            <FixRunsPanel runs={fixRuns} busyKey={busyKey} onCancel={onCancelFixRun} />
          ) : (
            <EmptyState title="No fix runs yet." detail="Fix Review runs will appear here after a completed swarm scan is selected from History." />
          )
        ) : null}

        {activeTab === 'snapshots' ? (
          <BackupListCard backups={backups} embedded />
        ) : null}
      </div>
    </section>
  );
}

function ProjectScoutCard({
  workspaceFilled,
  analysis,
  errorText,
  busy,
  onAnalyze,
}: {
  workspaceFilled: boolean;
  analysis: ReviewAnalysis | null;
  errorText: string;
  busy: boolean;
  onAnalyze: () => void;
}) {
  const recommendation = analysis?.recommendation;
  const topLanguages = analysis?.languages?.slice(0, 4) || [];
  const topMarkers = analysis?.markers?.slice(0, 4) || [];
  const laneFocuses = recommendation?.laneFocuses?.slice(0, 6) || [];
  const scoutWarnings = recommendation?.limits?.warnings || [];
  return (
    <div className="rounded-[18px] border border-amber-400/25 bg-amber-500/[0.055] px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-amber-700 dark:text-amber-200">Project Scout</div>
          <div className="mt-1 text-sm text-slate-600 dark:text-gray-400">
            {analysis ? `${analysis.projectName} scanned` : 'Scan the project shape before launching lanes.'}
          </div>
        </div>
        <button
          type="button"
          onClick={onAnalyze}
          disabled={!workspaceFilled || busy}
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-[14px] border border-amber-400/30 bg-amber-500/10 px-3 py-2 text-sm font-semibold text-amber-800 transition-colors hover:bg-amber-500/15 disabled:cursor-not-allowed disabled:opacity-50 dark:text-amber-100"
        >
          {busy ? <Loader2 size={15} className="animate-spin" /> : <SearchCheck size={15} />}
          {analysis ? 'Rescan' : 'Scout'}
        </button>
      </div>
      {errorText ? <div className="mt-3 rounded-xl border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-200">{errorText}</div> : null}
      {analysis && recommendation ? (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <ScoutMetric label="Files" value={String(analysis.fileCount)} />
            <ScoutMetric label="Lanes" value={String(recommendation.agents)} />
            <ScoutMetric label="Provider" value={recommendation.provider} />
            <ScoutMetric label="Model" value={recommendation.modelStrategy || 'CLI default'} />
          </div>
          {topLanguages.length ? (
            <div className="flex flex-wrap gap-2">
              {topLanguages.map((item) => <MetaPill key={item.name} label={`${item.name} ${item.files}`} />)}
            </div>
          ) : null}
          {topMarkers.length ? (
            <div className="flex flex-wrap gap-2">
              {topMarkers.map((item) => <MetaPill key={item.path} label={item.path} />)}
            </div>
          ) : null}
          {recommendation.reasons?.length ? (
            <ul className="space-y-1 text-xs leading-relaxed text-slate-600 dark:text-gray-400">
              {recommendation.reasons.slice(0, 4).map((reason) => <li key={reason}>- {reason}</li>)}
            </ul>
          ) : null}
          {scoutWarnings.length ? (
            <div className="rounded-[12px] border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-xs leading-relaxed text-amber-900 dark:text-amber-100">
              {scoutWarnings.slice(0, 3).map((warning) => <div key={warning}>- {warning}</div>)}
            </div>
          ) : null}
          {laneFocuses.length ? (
            <div className="flex flex-wrap gap-2">
              {laneFocuses.map((lane) => <MetaPill key={lane.focus} label={lane.focus} />)}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ScoutMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[12px] border border-slate-200/70 bg-white/45 px-3 py-2 dark:border-white/10 dark:bg-black/25">
      <div className="text-[9px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-gray-500">{label}</div>
      <div className="mt-1 truncate font-mono text-sm text-slate-800 dark:text-gray-100">{value}</div>
    </div>
  );
}

function ActivityTabButton({
  active,
  icon,
  label,
  count,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-[14px] border px-3 py-2 text-xs font-semibold transition-colors ${
        active
          ? 'border-emerald-500/45 bg-emerald-500/[0.14] text-emerald-900 dark:text-emerald-100'
          : 'border-slate-200/80 bg-white/60 text-slate-600 hover:bg-white dark:border-white/10 dark:bg-black/20 dark:text-gray-400 dark:hover:bg-white/[0.06] dark:hover:text-white'
      }`}
    >
      {icon}
      <span className="truncate">{label}</span>
      <span className={`rounded-full px-2 py-0.5 font-mono text-[10px] ${active ? 'bg-emerald-950/10 text-emerald-950 dark:bg-white/15 dark:text-white' : 'bg-slate-200/70 text-slate-600 dark:bg-white/10 dark:text-gray-300'}`}>{count}</span>
    </button>
  );
}

const HIVE_CELLS = [
  { left: 10, top: 12, delay: 0 },
  { left: 25, top: 8, delay: 0.3 },
  { left: 40, top: 12, delay: 0.6 },
  { left: 55, top: 8, delay: 0.9 },
  { left: 70, top: 12, delay: 1.2 },
  { left: 18, top: 31, delay: 1.5 },
  { left: 33, top: 35, delay: 1.8 },
  { left: 48, top: 31, delay: 2.1 },
  { left: 63, top: 35, delay: 2.4 },
  { left: 78, top: 31, delay: 2.7 },
  { left: 10, top: 54, delay: 3.0 },
  { left: 25, top: 58, delay: 3.3 },
  { left: 40, top: 54, delay: 3.6 },
  { left: 55, top: 58, delay: 3.9 },
  { left: 70, top: 54, delay: 4.2 },
];

const SWARM_DOTS = [
  { left: 17, top: 22, delay: 0.1 },
  { left: 37, top: 18, delay: 0.7 },
  { left: 59, top: 20, delay: 1.4 },
  { left: 73, top: 42, delay: 2.0 },
  { left: 29, top: 48, delay: 2.6 },
  { left: 47, top: 69, delay: 3.2 },
  { left: 66, top: 66, delay: 3.8 },
  { left: 20, top: 72, delay: 4.4 },
];

function HiveStandby({ historyCount }: { historyCount: number }) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <div className="swarm-standby-field relative min-h-[410px] overflow-hidden rounded-[20px] border border-dashed border-emerald-300/35 bg-slate-50/60 p-5 dark:border-emerald-300/20 dark:bg-black/25 sm:p-6">
      <div className="relative z-10 flex min-h-[350px] flex-col">
        <div className="max-w-sm">
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-400/30 bg-emerald-500/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.22em] text-emerald-700 dark:text-emerald-200">
            <Activity size={14} />
            Standby
          </div>
          <h3 className="mt-4 text-2xl font-black tracking-tight text-slate-900 dark:text-white">Waiting for a target scan.</h3>
          <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-gray-400">Dispatch a swarm from the control rail and this space becomes the live lane board.</p>
          <div className="mt-5 flex flex-wrap gap-2">
            <MetaPill label="read-only reviewers" />
            <MetaPill label="scratch workspaces" />
            <MetaPill label={`${historyCount} in history`} mono />
          </div>
        </div>
      </div>
      <div className="swarm-hive-layer pointer-events-none absolute bottom-12 right-4 top-10 w-[62%] max-w-[540px] opacity-95 max-sm:inset-x-4 max-sm:bottom-20 max-sm:top-44 max-sm:w-auto">
        {HIVE_CELLS.map((cell, index) => (
          <motion.div
            key={`cell-${index}`}
            className="absolute h-20 w-20 border border-amber-300/30 bg-amber-300/[0.055] shadow-[inset_0_0_22px_rgba(251,191,36,0.05)] dark:border-amber-200/[0.18] dark:bg-amber-200/[0.04]"
            style={{ left: `${cell.left}%`, top: `${cell.top}%`, clipPath: 'polygon(25% 6%, 75% 6%, 100% 50%, 75% 94%, 25% 94%, 0 50%)' }}
            animate={shouldReduceMotion ? { opacity: 0.52, scale: 1 } : { opacity: [0.38, 0.72, 0.38], scale: [0.98, 1.04, 0.98] }}
            transition={shouldReduceMotion ? undefined : { duration: 5.5, delay: cell.delay, repeat: Infinity, ease: 'easeInOut' }}
          />
        ))}
        {SWARM_DOTS.map((dot, index) => (
          <motion.span
            key={`dot-${index}`}
            className="absolute h-2.5 w-2.5 rounded-full bg-emerald-300/85 shadow-[0_0_18px_rgba(110,231,183,0.68)]"
            style={{ left: `${dot.left}%`, top: `${dot.top}%` }}
            animate={shouldReduceMotion ? { x: 0, y: 0, opacity: 0.72 } : { x: [0, 10, -5, 0], y: [0, -8, 6, 0], opacity: [0.35, 1, 0.55, 0.35] }}
            transition={shouldReduceMotion ? undefined : { duration: 6, delay: dot.delay, repeat: Infinity, ease: 'easeInOut' }}
          />
        ))}
      </div>
    </div>
  );
}

type LimitAwareRecord = {
  agentCount?: number;
  taskCount?: number;
  progress?: ReviewProgress;
  maxParallel?: number;
  maxWorkers?: number;
  maxAgents?: number;
  limitWarnings?: string[];
  warnings?: string[];
  limits?: LimitMetadata;
};

function isInFlightStatus(status: string): boolean {
  return status === 'queued' || status === 'running';
}

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ');
}

type ReviewPresentationJob = Pick<ReviewJob, 'status'> & Partial<Pick<ReviewJob, 'severityCounts' | 'progress' | 'agents'>>;

export function reviewFindingTotal(job: Pick<ReviewPresentationJob, 'severityCounts'>): number {
  const counts = job.severityCounts || { high: 0, medium: 0, low: 0 };
  return (counts.high || 0) + (counts.medium || 0) + (counts.low || 0);
}

export function isPartialCancelledReview(job: ReviewPresentationJob): boolean {
  if (job.status !== 'cancelled') {
    return false;
  }
  if (reviewFindingTotal(job) > 0) {
    return true;
  }
  if ((job.progress?.done || 0) > 0) {
    return true;
  }
  return Boolean(job.agents?.some((agent) => (agent.findings || 0) > 0 || agent.status === 'done'));
}

export function reviewDisplayStatus(job: ReviewPresentationJob): string {
  return isPartialCancelledReview(job) ? 'partial/cancelled' : statusLabel(job.status);
}

export function canBrowseReviewFindings(job: ReviewPresentationJob): boolean {
  if (job.status === 'completed' || job.status === 'completed_with_warnings' || job.status === 'failed') {
    return true;
  }
  return isPartialCancelledReview(job);
}

export function canStartFixReviewForJob(job: Pick<ReviewJob, 'status' | 'severityCounts'>): boolean {
  return (job.status === 'completed' || job.status === 'completed_with_warnings') && reviewFindingTotal(job) > 0;
}

export function reviewAgentVisibility(agents: ReviewAgentRecord[] = [], initialLimit = 8): { visible: ReviewAgentRecord[]; overflow: ReviewAgentRecord[]; total: number } {
  const safeLimit = Math.max(0, initialLimit);
  return {
    visible: agents.slice(0, safeLimit),
    overflow: agents.slice(safeLimit),
    total: agents.length,
  };
}

function statusTone(status: string): string {
  if (status === 'failed') {
    return 'text-red-300 bg-red-500/10 border-red-500/25';
  }
  if (status === 'completed') {
    return 'text-emerald-300 bg-emerald-500/10 border-emerald-500/25';
  }
  if (status === 'completed_with_warnings') {
    return 'text-amber-300 bg-amber-500/10 border-amber-500/25';
  }
  if (status === 'cancelled') {
    return 'text-amber-300 bg-amber-500/10 border-amber-500/25';
  }
  return 'text-indigo-300 bg-indigo-500/10 border-indigo-500/25';
}

function firstNumber(...values: Array<number | undefined>): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
      return value;
    }
  }
  return null;
}

function warningList(record: LimitAwareRecord): string[] {
  return [
    ...(record.limitWarnings || []),
    ...(record.warnings || []),
    ...(record.limits?.warnings || []),
    ...(record.limits?.accountHomeWarning ? [record.limits.accountHomeWarning] : []),
  ].filter((item, index, items) => item.trim().length > 0 && items.indexOf(item) === index);
}

function mergePendingChatMessages(fetched: ChatMessageRecord[], current: ChatMessageRecord[]): ChatMessageRecord[] {
  const fetchedIds = new Set(fetched.map((message) => message.id));
  const pending = current.filter((message) => (
    (message.id.startsWith('local-') || message.id.startsWith('assistant-') || message.id.startsWith('error-')) &&
    !fetchedIds.has(message.id)
  ));
  return pending.length ? [...fetched, ...pending] : fetched;
}

function maxParallelFor(record: LimitAwareRecord): number | null {
  return firstNumber(record.maxParallel, record.maxWorkers, record.maxAgents, record.progress?.maxParallel, record.progress?.maxWorkers, record.limits?.maxParallel, record.limits?.maxWorkers);
}

function progressFor(progress: ReviewProgress | undefined, fallbackTotal: number): ReviewProgress {
  return progress || { total: fallbackTotal, queued: 0, running: 0, done: 0, failed: 0, cancelled: 0 };
}

function ProgressCounts({ progress, total }: { progress: ReviewProgress; total: number }) {
  const rows = [
    ['Queued', progress.queued || 0],
    ['Running', progress.running || 0],
    ['Done', progress.done || 0],
    ['Failed', progress.failed || 0],
    ['Cancelled', progress.cancelled || 0],
  ] as const;
  return (
    <div className="mb-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-5">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-[12px] border border-slate-200/70 bg-white/55 px-3 py-2 dark:border-white/10 dark:bg-black/25">
          <div className="font-mono text-base font-semibold text-slate-900 dark:text-white">{value}/{total}</div>
          <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500 dark:text-gray-500">{label}</div>
        </div>
      ))}
    </div>
  );
}

function LimitNotice({ record, requested }: { record: LimitAwareRecord; requested: number }) {
  const maxParallel = maxParallelFor(record);
  const warnings = warningList(record);
  const shouldShowParallel = maxParallel !== null;
  if (!shouldShowParallel && warnings.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 rounded-[16px] border border-amber-500/25 bg-amber-500/[0.09] px-4 py-3 text-sm text-amber-800 dark:text-amber-100">
      <div className="flex items-start gap-2">
        <AlertTriangle size={16} className="mt-0.5 shrink-0" />
        <div>
          {shouldShowParallel ? (
            <div>
              Backend max parallel: <span className="font-mono">{maxParallel}</span>
              {requested > maxParallel ? <span>. {requested} requested item{requested === 1 ? '' : 's'} will queue behind that limit.</span> : null}
            </div>
          ) : null}
          {warnings.length ? (
            <ul className={shouldShowParallel ? 'mt-2 space-y-1' : 'space-y-1'}>
              {warnings.map((warning) => <li key={warning}>{warning}</li>)}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function FixRunsPanel({
  runs,
  busyKey,
  onCancel,
}: {
  runs: FixRun[];
  busyKey: string | null;
  onCancel: (run: FixRun) => void;
}) {
  if (!runs.length) {
    return null;
  }
  const activeRuns = runs.filter((run) => isInFlightStatus(run.status)).length;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Fix runs</div>
          <div className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">Guarded Review fixes</div>
        </div>
        <MetaPill label={`${activeRuns} active`} mono />
      </div>
      {runs.map((run) => (
        <FixRunCard
          key={run.id}
          run={run}
          cancelBusy={busyKey === `fix-run-cancel-${run.id}`}
          onCancel={() => onCancel(run)}
        />
      ))}
    </div>
  );
}

function FixRunCard({
  run,
  cancelBusy,
  onCancel,
}: {
  run: FixRun;
  cancelBusy: boolean;
  onCancel: () => void;
}) {
  const taskTotal = run.taskCount || run.tasks?.length || 0;
  const progress = progressFor(run.progress, taskTotal);
  const total = Math.max(progress.total || taskTotal, 1);
  const finished = (progress.done || 0) + (progress.failed || 0) + (progress.cancelled || 0);
  const percent = Math.min(100, Math.round((finished / total) * 100));
  const inFlight = isInFlightStatus(run.status);
  const reviewId = run.reviewId || run.reviewJobId || '';
  const backupValue = run.sourceBackup?.id || run.backup?.id || run.sourceBackup?.error || run.backup?.error || 'Pending';

  return (
    <div className="rounded-[30px] border border-slate-200/80 bg-white/80 p-5 shadow-[0_18px_45px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-2xl">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.22em] ${statusTone(run.status)}`}>{statusLabel(run.status)}</span>
            <MetaPill label="Fix Review" />
            {run.provider ? <MetaPill label={run.provider} mono /> : null}
            {reviewId ? <MetaPill label={reviewId} mono /> : null}
            {run.cancelRequested && inFlight ? <MetaPill label="cancel pending" /> : null}
          </div>
          <h3 className="mt-3 text-lg font-bold tracking-tight text-slate-900 dark:text-white">{run.title || run.id}</h3>
          <div className="mt-2 break-all font-mono text-xs text-slate-500 dark:text-gray-500">{run.workspace}</div>
        </div>
        {inFlight ? (
          <ActionButton
            label={cancelBusy ? 'Cancelling...' : 'Cancel'}
            icon={cancelBusy ? <Loader2 size={16} className="animate-spin" /> : <X size={16} />}
            busy={cancelBusy || run.cancelRequested === true}
            tone="danger"
            onClick={onCancel}
          />
        ) : null}
      </div>

      <div className="mt-5">
        <ProgressCounts progress={progress} total={total} />
        <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10">
          <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${percent}%` }} />
        </div>
      </div>

      <LimitNotice record={run} requested={progress.total || taskTotal || total} />

      <FixRunPlanSection plan={run.plan} requestedMaxAgents={run.requestedMaxAgents} maxAgents={run.maxAgents} />

      <div className="mt-5 grid gap-3 lg:grid-cols-2">
        <InspectorRow label="Run" value={run.id} mono />
        <InspectorRow label="Report" value={run.reportPath || 'Pending'} mono />
        <InspectorRow label="Backup" value={backupValue} mono />
        {run.restoreCommand ? <InspectorRow label="Restore" value={run.restoreCommand} mono /> : null}
        <InspectorRow label="Artifacts" value={run.artifactDir || 'Pending'} mono />
      </div>

      {run.error ? <div className="mt-4 rounded-2xl border border-red-500/25 bg-red-500/10 px-4 py-3 text-sm text-red-100">{run.error}</div> : null}

      {run.tasks?.length ? (
        <div className="mt-5 grid gap-2 md:grid-cols-2">
          {run.tasks.slice(0, 8).map((task) => (
            <FixTaskTile key={task.id} task={task} />
          ))}
          {run.tasks.length > 8 ? (
            <div className="rounded-2xl border border-dashed border-slate-200/80 bg-slate-50/40 px-3 py-3 text-xs text-slate-500 dark:border-white/10 dark:bg-black/20 dark:text-gray-400">
              +{run.tasks.length - 8} more task{run.tasks.length - 8 === 1 ? '' : 's'} not shown — see report for full list.
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function FixRunPlanSection({
  plan,
  requestedMaxAgents,
  maxAgents,
}: {
  plan?: FixRunPlan;
  requestedMaxAgents?: number;
  maxAgents?: number;
}) {
  if (!plan) {
    return null;
  }
  const isAi = (plan.source || '').toLowerCase() === 'ai';
  const tone = isAi
    ? 'border-emerald-400/40 bg-emerald-500/[0.06] dark:border-emerald-400/20 dark:bg-emerald-500/[0.05]'
    : 'border-amber-400/40 bg-amber-500/[0.05] dark:border-amber-400/20 dark:bg-amber-500/[0.04]';
  const labelTone = isAi ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-700 dark:text-amber-300';
  const recommended = typeof plan.recommendedAgentCount === 'number' && plan.recommendedAgentCount > 0 ? plan.recommendedAgentCount : null;
  return (
    <div className={`mt-5 rounded-2xl border px-4 py-4 ${tone}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`text-[10px] font-bold uppercase tracking-[0.22em] ${labelTone}`}>
          {isAi ? 'AI orchestrator' : 'Deterministic plan'}
        </span>
        {plan.provider ? <MetaPill label={plan.provider} mono /> : null}
        {recommended !== null ? <MetaPill label={`${recommended} agent${recommended === 1 ? '' : 's'} recommended`} mono /> : null}
        {typeof plan.taskCount === 'number' && plan.taskCount > 0 ? <MetaPill label={`${plan.taskCount} task${plan.taskCount === 1 ? '' : 's'}`} mono /> : null}
        {typeof requestedMaxAgents === 'number' && typeof maxAgents === 'number' && maxAgents !== requestedMaxAgents ? (
          <MetaPill label={`capped to ${maxAgents} of ${requestedMaxAgents}`} />
        ) : null}
      </div>
      {plan.summary ? (
        <div className="mt-2 text-sm font-semibold text-slate-900 dark:text-white">{plan.summary}</div>
      ) : null}
      {plan.rationale ? (
        <div className="mt-1 text-xs text-slate-600 dark:text-gray-400">{plan.rationale}</div>
      ) : null}
      {!isAi && plan.fallbackReason ? (
        <div className="mt-2 text-[11px] uppercase tracking-[0.18em] text-amber-700 dark:text-amber-300">
          Fallback reason: <span className="font-mono normal-case tracking-normal">{plan.fallbackReason}</span>
        </div>
      ) : null}
    </div>
  );
}

function FixTaskTile({ task }: { task: FixTaskRecord }) {
  const provider = (task.provider || '').toLowerCase();
  const providerTone =
    provider === 'claude'
      ? 'border-violet-400/40 text-violet-700 dark:border-violet-400/30 dark:text-violet-200'
      : provider === 'codex'
        ? 'border-sky-400/40 text-sky-700 dark:border-sky-400/30 dark:text-sky-200'
        : 'border-slate-300/60 text-slate-600 dark:border-white/10 dark:text-gray-300';
  const effort = (task.reasoningEffort || '').trim();
  const phase = typeof task.phase === 'number' ? task.phase : null;
  const dependsOn = task.dependsOn?.filter(Boolean) ?? [];
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white/70 px-3 py-3 dark:border-white/5 dark:bg-black/30">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 truncate font-mono text-xs text-slate-700 dark:text-gray-300">{task.title || task.id}</div>
        <div className="shrink-0 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-gray-500">{statusLabel(task.status)}</div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {provider ? (
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.18em] ${providerTone}`}>{provider}</span>
        ) : null}
        {effort ? <MetaPill label={`effort: ${effort}`} /> : null}
        {phase !== null ? <MetaPill label={`phase ${phase}`} /> : null}
        {task.severity ? <MetaPill label={task.severity} /> : null}
        {task.category ? <MetaPill label={task.category} /> : null}
      </div>
      {task.rationale ? <div className="mt-2 line-clamp-2 text-xs text-slate-600 dark:text-gray-400">{task.rationale}</div> : null}
      {task.detail && !task.rationale ? <div className="mt-2 line-clamp-2 text-xs text-slate-500 dark:text-gray-500">{task.detail}</div> : null}
      {dependsOn.length ? (
        <div className="mt-2 text-[10px] uppercase tracking-[0.18em] text-slate-500 dark:text-gray-500">
          depends on <span className="font-mono normal-case tracking-normal text-slate-600 dark:text-gray-400">{dependsOn.join(', ')}</span>
        </div>
      ) : null}
      {task.error ? <div className="mt-2 line-clamp-2 text-xs text-red-600 dark:text-red-300">{task.error}</div> : null}
    </div>
  );
}

function BackupListCard({ backups, embedded = false }: { backups: BackupRecord[]; embedded?: boolean }) {
  const recent = backups.slice(0, 8);
  return (
    <div className={embedded ? 'rounded-[24px] border border-slate-200/80 bg-white/55 p-4 dark:border-white/10 dark:bg-black/20 sm:p-5' : 'rounded-[30px] border border-slate-200/80 bg-white/80 p-5 shadow-[0_18px_45px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-2xl'}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Source snapshots</div>
          <div className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">CLADEX-managed backups</div>
        </div>
        <MetaPill label={`${backups.length} total`} mono />
      </div>
      {recent.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-slate-200/80 bg-slate-50/60 px-4 py-3 text-sm text-slate-500 dark:border-white/10 dark:bg-black/20 dark:text-gray-400">
          No snapshots yet. Reviews with the snapshot toggle on, or "Save snapshot only", will appear here.
        </div>
      ) : (
        <ul className="mt-4 space-y-2">
          {recent.map((backup) => (
            <li key={backup.id} className="rounded-2xl border border-slate-200/80 bg-white/70 px-3 py-3 dark:border-white/5 dark:bg-black/30">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="font-mono text-xs text-slate-700 dark:text-gray-300">{backup.id}</div>
                <div className="text-[10px] uppercase tracking-[0.18em] text-slate-500 dark:text-gray-500">{backup.reason || 'manual'}</div>
              </div>
              <div className="mt-1 break-all text-xs text-slate-500 dark:text-gray-500">{backup.workspace}</div>
              <div className="mt-1 text-[11px] text-slate-400 dark:text-gray-500">{backup.createdAt}</div>
            </li>
          ))}
        </ul>
      )}
      {backups.length ? (
        <div className="mt-3 text-[11px] text-slate-500 dark:text-gray-500">
          Restore is CLI-only: <span className="font-mono">cladex backup restore &lt;id&gt; --confirm &lt;id&gt;</span>.
        </div>
      ) : null}
    </div>
  );
}

function ReviewJobCard({
  job,
  activeFixRun,
  fixPlanBusy,
  fixReviewBusy,
  cancelBusy,
  onFixPlan,
  onFixReview,
  onCancel,
}: {
  job: ReviewJob;
  activeFixRun?: FixRun;
  fixPlanBusy: boolean;
  fixReviewBusy: boolean;
  cancelBusy: boolean;
  onFixPlan: () => void;
  onFixReview: (options?: { allowSelfFix?: boolean }) => void;
  onCancel: () => void;
}) {
  const progress = progressFor(job.progress, job.agentCount || 0);
  const total = Math.max(progress.total || job.agentCount || 0, 1);
  const finished = (progress.done || 0) + (progress.failed || 0) + (progress.cancelled || 0);
  const percent = Math.min(100, Math.round((finished / total) * 100));
  const inFlight = isInFlightStatus(job.status);
  const canExploreFindings = canBrowseReviewFindings(job);
  const partialCancelled = isPartialCancelledReview(job);
  const severity = job.severityCounts || { high: 0, medium: 0, low: 0 };
  const totalFindings = reviewFindingTotal(job);
  const canFixReview = canStartFixReviewForJob(job);
  const lanes = reviewAgentVisibility(job.agents || []);
  const renderAgentCard = (agent: ReviewAgentRecord) => {
    const showDetail = agent.detail && ['failed', 'cancelled'].includes(agent.status);
    return (
    <div key={agent.id} className="grid grid-cols-[74px_minmax(0,1fr)_76px] items-center gap-3 border-b border-slate-200/70 px-1 py-2.5 last:border-b-0 dark:border-white/[0.08]">
      <div className="font-mono text-xs font-semibold text-slate-800 dark:text-gray-200">{agent.id}</div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-slate-800 dark:text-gray-200">{agent.focus || 'review'}</div>
        <div className="mt-0.5 text-xs text-slate-500 dark:text-gray-500">
          {agent.assignedFiles} files{showDetail ? ` - ${agent.detail}` : ''}
        </div>
      </div>
      <div className="text-right">
        <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500 dark:text-gray-500">{agent.status}</div>
        <div className="mt-0.5 font-mono text-xs text-slate-700 dark:text-gray-300">{agent.findings} found</div>
      </div>
    </div>
  );
  };

  return (
    <article className="swarm-run-card rounded-[20px] border border-slate-200/80 bg-white/[0.78] p-4 shadow-[0_18px_48px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-white/[0.035] dark:shadow-[0_22px_70px_rgba(0,0,0,0.32)] sm:p-5">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.2em] ${statusTone(job.status)}`}>{reviewDisplayStatus(job)}</span>
            <MetaPill label={`${job.provider} swarm`} mono />
            <MetaPill label={`${job.agentCount} lane${job.agentCount === 1 ? '' : 's'}`} mono />
            {job.selfReview ? <MetaPill label="CLADEX self-review" /> : null}
            {job.cancelRequested && inFlight ? <MetaPill label="cancel pending" /> : null}
            {partialCancelled ? <MetaPill label="partial findings" /> : null}
          </div>
          <h3 className="mt-3 text-xl font-black tracking-tight text-slate-900 dark:text-white">{job.title || job.id}</h3>
          <div className="mt-2 break-all font-mono text-xs text-slate-500 dark:text-gray-500">{job.workspace}</div>

          <div className="mt-5">
            <ProgressCounts progress={progress} total={total} />
            <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10">
              <div className="h-full rounded-full bg-emerald-400 transition-all" style={{ width: `${percent}%` }} />
            </div>
          </div>

          <LimitNotice record={job} requested={job.agentCount || total} />

          {job.error ? <div className="mt-4 rounded-[16px] border border-red-500/25 bg-red-500/10 px-4 py-3 text-sm text-red-700 dark:text-red-100">{job.error}</div> : null}

          {lanes.total ? (
            <div className="mt-5 rounded-[16px] border border-slate-200/80 bg-white/55 p-3 dark:border-white/[0.08] dark:bg-black/20">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-gray-500">Lane board</div>
                <MetaPill label={`${finished}/${total} settled`} mono />
              </div>
              <div>
                {lanes.visible.map(renderAgentCard)}
              </div>
              {lanes.overflow.length ? (
                <details className="mt-2 rounded-[12px] border border-dashed border-slate-200/80 px-3 py-2 dark:border-white/10">
                  <summary className="cursor-pointer text-xs font-semibold text-emerald-700 dark:text-emerald-300">
                    Show all {lanes.total} lanes ({lanes.overflow.length} more)
                  </summary>
                  <div className="mt-2">
                    {lanes.overflow.map(renderAgentCard)}
                  </div>
                </details>
              ) : null}
            </div>
          ) : null}
        </div>

        <aside className="space-y-3 rounded-[18px] border border-slate-200/80 bg-slate-50/70 p-4 dark:border-white/[0.08] dark:bg-black/25">
          <div className="flex flex-wrap gap-2">
            <ActionButton
              label={fixPlanBusy ? 'Planning...' : 'Fix Plan'}
              icon={fixPlanBusy ? <Loader2 size={16} className="animate-spin" /> : <FileText size={16} />}
              busy={fixPlanBusy}
              disabled={inFlight}
              onClick={onFixPlan}
            />
            {canFixReview ? (
              <ActionButton
                label={activeFixRun ? 'Fix running' : fixReviewBusy ? 'Starting fix...' : 'Fix Review'}
                icon={fixReviewBusy ? <Loader2 size={16} className="animate-spin" /> : <Wrench size={16} />}
                busy={fixReviewBusy || Boolean(activeFixRun)}
                onClick={() => {
                  const message = job.selfReview
                    ? 'Start write-capable CLADEX self-fix? This is separate from self-review and creates a source backup before edits.'
                    : 'Start a guarded Fix Review run? CLADEX will create a source backup before any worker edits.';
                  if (window.confirm(message)) {
                    onFixReview({ allowSelfFix: job.selfReview === true });
                  }
                }}
              />
            ) : null}
            {!inFlight && totalFindings === 0 ? <MetaPill label="no fixes needed" /> : null}
            {inFlight ? (
              <ActionButton
                label={cancelBusy ? 'Cancelling...' : 'Cancel'}
                icon={cancelBusy ? <Loader2 size={16} className="animate-spin" /> : <X size={16} />}
                busy={cancelBusy || job.cancelRequested === true}
                tone="danger"
                onClick={onCancel}
              />
            ) : null}
          </div>

          <div className="rounded-[14px] border border-slate-200/80 bg-white/60 px-3 py-3 dark:border-white/[0.08] dark:bg-black/25">
            <div className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-gray-500">Findings</div>
            <div className="mt-2 grid grid-cols-3 gap-2 text-center">
              <SeverityCounter label="High" value={severity.high || 0} tone="red" />
              <SeverityCounter label="Medium" value={severity.medium || 0} tone="amber" />
              <SeverityCounter label="Low" value={severity.low || 0} tone="slate" />
            </div>
          </div>

          <div className="rounded-[14px] border border-slate-200/80 bg-white/60 px-3 py-3 dark:border-white/[0.08] dark:bg-black/25">
            <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-gray-500">Artifacts</div>
            <ArtifactLine label="Report" value={job.reportPath || 'Pending'} />
            <ArtifactLine label="Fix plan" value={job.fixPlanPath || 'Not generated'} />
            <ArtifactLine label="Backup" value={job.sourceBackup?.id || job.sourceBackup?.error || 'Not created'} />
          </div>
        </aside>
      </div>

      {job.reportPreview ? (
        <details className="mt-5">
          <summary className="cursor-pointer text-sm font-semibold text-indigo-300">Report preview</summary>
          <pre className="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap rounded-[16px] border border-white/5 bg-black p-4 text-xs leading-relaxed text-gray-300">{job.reportPreview}</pre>
        </details>
      ) : null}

      {canExploreFindings ? (
        <FindingsExplorer jobId={job.id} totalFindings={totalFindings} partial={partialCancelled} />
      ) : null}
    </article>
  );
}

function SeverityCounter({ label, value, tone }: { label: string; value: number; tone: 'red' | 'amber' | 'slate' }) {
  const toneClass = tone === 'red'
    ? 'text-red-700 dark:text-red-200'
    : tone === 'amber'
      ? 'text-amber-700 dark:text-amber-200'
      : 'text-slate-700 dark:text-gray-200';
  return (
    <div>
      <div className={`font-mono text-lg font-semibold ${toneClass}`}>{value}</div>
      <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500 dark:text-gray-500">{label}</div>
    </div>
  );
}

function ArtifactLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[70px_minmax(0,1fr)] gap-2 border-t border-slate-200/70 py-2 first:border-t-0 dark:border-white/[0.08]">
      <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500 dark:text-gray-500">{label}</div>
      <div className="break-all font-mono text-xs text-slate-700 dark:text-gray-300">{value}</div>
    </div>
  );
}

function FindingsExplorer({ jobId, totalFindings, partial = false }: { jobId: string; totalFindings: number; partial?: boolean }) {
  const [open, setOpen] = useState(false);
  const [findings, setFindings] = useState<ReviewFinding[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [requested, setRequested] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [severityFilter, setSeverityFilter] = useState<Record<'high' | 'medium' | 'low', boolean>>({
    high: true,
    medium: true,
    low: true,
  });
  const [categoryFilter, setCategoryFilter] = useState<string>('');

  useEffect(() => {
    setFindings(null);
    setErrorText('');
    setRequested(false);
    setCategoryFilter('');
  }, [jobId]);

  useEffect(() => {
    if (!open || findings !== null || loading || requested) return;
    let cancelled = false;
    setRequested(true);
    setLoading(true);
    setErrorText('');
    api
      .reviewFindings(jobId)
      .then((payload) => {
        if (!cancelled) setFindings(payload?.findings || []);
      })
      .catch((err) => {
        if (!cancelled) setErrorText(err instanceof Error ? err.message : 'Failed to load findings.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, findings, loading, requested, jobId]);

  const categories = Array.from(new Set((findings || []).map((item) => item.category || 'unknown'))).sort();
  const filtered = (findings || []).filter((item) => {
    const sev = (item.severity || 'medium') as 'high' | 'medium' | 'low';
    if (!severityFilter[sev]) return false;
    if (categoryFilter && (item.category || '') !== categoryFilter) return false;
    return true;
  });

  const handleExport = () => {
    if (!findings) return;
    const blob = new Blob([JSON.stringify({ jobId, findings }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${jobId}-findings.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <details className="mt-4" open={open} onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-sm font-semibold text-indigo-300">
        {partial ? 'Partial findings explorer' : 'Findings explorer'}{totalFindings > 0 ? ` (${totalFindings})` : ''}
      </summary>
      <div className="mt-3 rounded-2xl border border-white/5 bg-black/40 p-4">
        {loading ? <div className="text-xs text-gray-400">Loading findings...</div> : null}
        {errorText ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-red-300">
            <span>{errorText}</span>
            <button
              onClick={() => {
                setErrorText('');
                setRequested(false);
              }}
              className="rounded-full border border-red-400/40 bg-red-500/10 px-2.5 py-1 font-semibold text-red-100 hover:bg-red-500/20"
            >
              Retry
            </button>
          </div>
        ) : null}
        {findings !== null && !loading && !errorText ? (
          <>
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              {(['high', 'medium', 'low'] as const).map((sev) => (
                <button
                  key={sev}
                  onClick={() => setSeverityFilter((prev) => ({ ...prev, [sev]: !prev[sev] }))}
                  className={`rounded-full border px-2.5 py-1 font-semibold transition-colors ${
                    severityFilter[sev]
                      ? sev === 'high'
                        ? 'border-red-500/60 bg-red-500/20 text-red-100'
                        : sev === 'medium'
                          ? 'border-amber-500/60 bg-amber-500/20 text-amber-100'
                          : 'border-slate-500/60 bg-slate-500/20 text-slate-100'
                      : 'border-white/10 bg-white/[0.03] text-gray-400'
                  }`}
                >
                  {sev}
                </button>
              ))}
              <select
                value={categoryFilter}
                onChange={(event) => setCategoryFilter(event.target.value)}
                className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-[11px] text-gray-200"
              >
                <option value="">All categories</option>
                {categories.map((cat) => (
                  <option key={cat} value={cat}>{cat}</option>
                ))}
              </select>
              <span className="ml-auto flex items-center gap-2">
                <span className="text-gray-400">{filtered.length} / {findings.length} shown</span>
                <button
                  onClick={handleExport}
                  className="rounded-full border border-indigo-500/40 bg-indigo-500/20 px-2.5 py-1 font-semibold text-indigo-100 hover:bg-indigo-500/30"
                >
                  Export JSON
                </button>
              </span>
            </div>
            {filtered.length === 0 ? (
              <div className="mt-3 text-xs text-gray-500">No findings match the selected filters.</div>
            ) : (
              <ul className="mt-3 max-h-80 space-y-2 overflow-y-auto">
                {filtered.slice(0, 200).map((item, index) => (
                  <li key={item.id || `${item.path}:${item.line}:${index}`} className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2">
                    <div className="flex flex-wrap items-baseline gap-2 text-[11px]">
                      <span className={`rounded-full px-2 py-0.5 font-bold uppercase tracking-[0.18em] ${
                        item.severity === 'high' ? 'bg-red-500/30 text-red-100'
                          : item.severity === 'medium' ? 'bg-amber-500/30 text-amber-100'
                          : 'bg-slate-500/30 text-slate-100'
                      }`}>{item.severity || 'medium'}</span>
                      <span className="font-mono text-gray-300">{item.id || ''}</span>
                      <span className="text-gray-400">{item.category || 'uncategorized'}</span>
                      {item.agentId ? <span className="text-gray-500">via {item.agentId}</span> : null}
                    </div>
                    <div className="mt-1 text-sm font-semibold text-white">{item.title || 'Finding'}</div>
                    <div className="mt-0.5 break-all font-mono text-[11px] text-gray-400">{item.path || '.'}{item.line ? `:${item.line}` : ''}</div>
                    {item.detail ? (
                      <div className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-gray-400">{item.detail}</div>
                    ) : null}
                    {item.recommendation ? (
                      <div className="mt-1 text-xs text-gray-300">→ {item.recommendation}</div>
                    ) : null}
                  </li>
                ))}
                {filtered.length > 200 ? (
                  <li className="text-xs text-gray-500">... showing first 200 of {filtered.length}. Export JSON for the full set.</li>
                ) : null}
              </ul>
            )}
          </>
        ) : null}
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// LiveFeed — operator console (flat Discord/Slack/ChatGPT-style chat feed)
//
// Native chat apps don't use floating bubbles. They use a flat message feed
// with the avatar in the left margin, the sender name + timestamp on the
// first message of a run, and subsequent messages stacked tightly under it.
// Hover reveals secondary actions (a full timestamp, eventually copy/react).
// We borrow that vocabulary directly because operators have years of muscle
// memory for it.
//
// Key affordances:
//   - flat message rows, NO bubbles. Just avatar + body on the surface.
//   - sender grouping with 5-min gap (Discord/Slack convention).
//   - sticky day separator (thin rule with centered date pill).
//   - inline `code spans` and ```triple-backtick``` blocks.
//   - URL linkify.
//   - hover row reveals absolute timestamp on the right margin.
//   - auto-scroll on new messages — but only when user is pinned to bottom.
//     If they scrolled up, a floating "↓ N new" pill appears instead.
//   - typing indicator inline at the bottom of the feed while sending.
//   - Slack-style composer: textarea with send button in the corner,
//     hint line below. Auto-grows to ~7 lines, then scrolls inside.
//   - Enter sends, Shift+Enter newline (with hint shown so users discover it).
//   - status pill on the conversation header (LIVE / STANDBY / OFFLINE).
//   - sidebar channel list with `#` icon and live LED, Discord style.
// ---------------------------------------------------------------------------

const _CHAT_DAY_FORMATTER = typeof Intl !== 'undefined'
  ? new Intl.DateTimeFormat(undefined, { weekday: 'long' })
  : null;
const _CHAT_LONG_DATE_FORMATTER = typeof Intl !== 'undefined'
  ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
  : null;
const _CHAT_TIME_FORMATTER = typeof Intl !== 'undefined'
  ? new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
  : null;

function chatParseTimestamp(timestamp: string | undefined): Date | null {
  if (!timestamp) return null;
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed;
}

function chatDayKey(timestamp: string | undefined): string {
  const parsed = chatParseTimestamp(timestamp);
  if (!parsed) return 'unknown';
  return `${parsed.getFullYear()}-${parsed.getMonth() + 1}-${parsed.getDate()}`;
}

function chatDayLabel(timestamp: string | undefined): string {
  const parsed = chatParseTimestamp(timestamp);
  if (!parsed) return 'Earlier';
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const compare = new Date(parsed);
  compare.setHours(0, 0, 0, 0);
  const diffDays = Math.round((today.getTime() - compare.getTime()) / 86_400_000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays > 1 && diffDays < 7 && _CHAT_DAY_FORMATTER) return _CHAT_DAY_FORMATTER.format(parsed);
  if (_CHAT_LONG_DATE_FORMATTER) return _CHAT_LONG_DATE_FORMATTER.format(parsed);
  return parsed.toDateString();
}

function chatTimeLabel(timestamp: string | undefined): string {
  const parsed = chatParseTimestamp(timestamp);
  if (!parsed) return '';
  if (_CHAT_TIME_FORMATTER) return _CHAT_TIME_FORMATTER.format(parsed);
  return `${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}`;
}

function chatSenderInitials(name: string | undefined, role: 'user' | 'assistant'): string {
  if (role === 'user') return 'OP';
  if (!name) return 'AI';
  const parts = name.replace(/[#@]/g, '').split(/[\s\-_·]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || 'AI';
}

function chatRenderInlineSpans(text: string, keyPrefix: string): React.ReactNode {
  // Split on backtick code spans. Anything else is plain text. URLs are
  // linkified in a second pass. Plain whitespace and newlines are preserved
  // by the parent container's `whitespace-pre-wrap`.
  const segments: React.ReactNode[] = [];
  const codeRe = /`([^`\n]+)`/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = codeRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push(...chatLinkify(text.slice(lastIndex, match.index), `${keyPrefix}-t-${i++}`));
    }
    segments.push(
      <code
        key={`${keyPrefix}-c-${i++}`}
        className="rounded-md border border-white/10 bg-black/35 px-1.5 py-0.5 font-mono text-[12px] text-amber-100/90 dark:text-amber-200/90"
      >
        {match[1]}
      </code>,
    );
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push(...chatLinkify(text.slice(lastIndex), `${keyPrefix}-t-${i++}`));
  }
  return segments.length ? segments : text;
}

function chatLinkify(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const urlRe = /\bhttps?:\/\/[^\s<>"')]+/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = urlRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      out.push(<React.Fragment key={`${keyPrefix}-p-${i++}`}>{text.slice(lastIndex, match.index)}</React.Fragment>);
    }
    const url = match[0];
    out.push(
      <a
        key={`${keyPrefix}-a-${i++}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all underline decoration-indigo-400/60 underline-offset-2 hover:text-indigo-300"
      >
        {url}
      </a>,
    );
    lastIndex = match.index + url.length;
  }
  if (lastIndex < text.length) {
    out.push(<React.Fragment key={`${keyPrefix}-p-${i++}`}>{text.slice(lastIndex)}</React.Fragment>);
  }
  return out.length ? out : [<React.Fragment key={`${keyPrefix}-p-only`}>{text}</React.Fragment>];
}

function chatRenderMessageBody(text: string): React.ReactNode {
  // Triple-backtick code blocks have priority over inline backticks.
  const segments: React.ReactNode[] = [];
  const blockRe = /```([a-zA-Z0-9_+\-.]*)\n?([\s\S]*?)```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = blockRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push(
        <span key={`b-${i++}`} className="whitespace-pre-wrap break-words">
          {chatRenderInlineSpans(text.slice(lastIndex, match.index), `b${i}`)}
        </span>,
      );
    }
    const lang = match[1] || '';
    const body = match[2] || '';
    segments.push(
      <pre
        key={`pre-${i++}`}
        className="my-2 max-w-full overflow-x-auto rounded-xl border border-white/10 bg-black/55 px-3 py-2.5 font-mono text-[12px] leading-relaxed text-amber-100/90 dark:text-amber-200/95"
      >
        {lang ? (
          <div className="mb-1 text-[9px] font-semibold uppercase tracking-[0.18em] text-amber-200/40">
            {lang}
          </div>
        ) : null}
        <code>{body}</code>
      </pre>,
    );
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push(
      <span key={`tail-${i++}`} className="whitespace-pre-wrap break-words">
        {chatRenderInlineSpans(text.slice(lastIndex), `t${i}`)}
      </span>,
    );
  }
  return segments.length ? segments : <span className="whitespace-pre-wrap break-words">{text}</span>;
}

interface ChatGroup {
  key: string;
  role: 'user' | 'assistant';
  senderName: string;
  messages: ChatMessageRecord[];
  firstTimestamp?: string;
  lastTimestamp?: string;
}

interface ChatDayBlock {
  key: string;
  label: string;
  groups: ChatGroup[];
}

function chatGroupMessages(messages: ChatMessageRecord[], assistantName: string): ChatDayBlock[] {
  const days: ChatDayBlock[] = [];
  const SENDER_GAP_MS = 5 * 60 * 1000;
  for (const message of messages) {
    const parsed = chatParseTimestamp(message.timestamp);
    const dayKey = chatDayKey(message.timestamp);
    let day = days[days.length - 1];
    if (!day || day.key !== dayKey) {
      day = { key: dayKey, label: chatDayLabel(message.timestamp), groups: [] };
      days.push(day);
    }
    const sender = message.senderName || (message.role === 'user' ? 'Operator' : assistantName);
    let group = day.groups[day.groups.length - 1];
    const lastTimestampDate = group ? chatParseTimestamp(group.lastTimestamp) : null;
    const sameSender = group && group.role === message.role && group.senderName === sender;
    const recentEnough = sameSender && lastTimestampDate && parsed
      ? parsed.getTime() - lastTimestampDate.getTime() <= SENDER_GAP_MS
      : false;
    if (sameSender && (recentEnough || !lastTimestampDate || !parsed)) {
      group.messages.push(message);
      group.lastTimestamp = message.timestamp || group.lastTimestamp;
    } else {
      day.groups.push({
        key: `${dayKey}-${day.groups.length}-${message.id}`,
        role: message.role,
        senderName: sender,
        messages: [message],
        firstTimestamp: message.timestamp,
        lastTimestamp: message.timestamp,
      });
    }
  }
  return days;
}

function chatRelativeTime(timestamp: string | undefined): string {
  const parsed = chatParseTimestamp(timestamp);
  if (!parsed) return '';
  const diffSec = Math.round((Date.now() - parsed.getTime()) / 1000);
  if (diffSec < 5) return 'now';
  if (diffSec < 60) return `${diffSec}s`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h`;
  return chatDayLabel(timestamp).toLowerCase();
}

function ChatStatusPill({ status }: { status: 'live' | 'standby' | 'down' }) {
  if (status === 'live') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-500/12 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-300">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
        </span>
        Online
      </span>
    );
  }
  if (status === 'standby') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-amber-300">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
        Starting
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-500/25 bg-slate-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">
      <WifiOff size={10} />
      Offline
    </span>
  );
}

function ChatAvatar({
  initials,
  role,
  relayType,
  pulse = false,
  size = 'md',
}: {
  initials: string;
  role: 'user' | 'assistant';
  relayType?: RelayType;
  pulse?: boolean;
  size?: 'sm' | 'md';
}) {
  const palette = role === 'user'
    ? 'bg-indigo-500/15 text-indigo-200 ring-indigo-400/30'
    : relayType === 'codex'
      ? 'bg-emerald-500/15 text-emerald-200 ring-emerald-400/30'
      : 'bg-amber-500/15 text-amber-200 ring-amber-400/30';
  const dimensions = size === 'sm' ? 'h-7 w-7 text-[10px]' : 'h-10 w-10 text-[12px]';
  const dotOffset = size === 'sm' ? '-bottom-0 -right-0' : '-bottom-0.5 -right-0.5';
  const dotSize = size === 'sm' ? 'h-2 w-2' : 'h-2.5 w-2.5';
  return (
    <div className={`relative flex shrink-0 select-none items-center justify-center rounded-full font-mono font-bold tracking-wider ring-1 ${dimensions} ${palette}`}>
      {initials}
      {pulse ? (
        <span className={`absolute ${dotOffset} flex ${dotSize}`}>
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
          <span className={`relative inline-flex ${dotSize} rounded-full bg-emerald-400 ring-2 ring-[#0a0a0c]`} />
        </span>
      ) : null}
    </div>
  );
}

function ChatTypingDots({ accentClass }: { accentClass: string }) {
  return (
    <span className={`inline-flex items-end gap-0.5 ${accentClass}`} aria-label="typing">
      <span className="block h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
      <span className="block h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
      <span className="block h-1.5 w-1.5 animate-bounce rounded-full bg-current" />
    </span>
  );
}

function LiveFeed({
  profiles,
  selectedProfileId,
  onSelectProfile,
  viewMode = 'simple',
}: {
  profiles: Profile[];
  selectedProfileId: string | null;
  onSelectProfile: (value: string) => void;
  viewMode?: ViewMode;
}) {
  const workspaces = Array.from(new Set(profiles.map((profile) => profile.workspace))).sort();
  const [activeWorkspace, setActiveWorkspace] = useState(workspaces[0] || '');
  const [messages, setMessages] = useState<ChatMessageRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [sending, setSending] = useState(false);
  const [draft, setDraft] = useState('');
  const [unreadBelow, setUnreadBelow] = useState(0);
  const sendingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const wasNearBottomRef = useRef(true);
  const previousMessageCountRef = useRef(0);
  const previousProfileIdRef = useRef<string | null>(null);
  const reduceMotion = useReducedMotion();
  const isAdvanced = viewMode === 'advanced';
  const workspaceProfiles = profiles.filter((profile) => profile.workspace === activeWorkspace);
  const activeProfile = workspaceProfiles.find((profile) => profileKey(profile) === selectedProfileId) || workspaceProfiles[0] || null;
  const activeProfileCanChat = Boolean(activeProfile?.running && activeProfile?.ready);
  const relayStatus: 'live' | 'standby' | 'down' = !activeProfile
    ? 'down'
    : activeProfile.running && activeProfile.ready
      ? 'live'
      : activeProfile.running
        ? 'standby'
        : 'down';
  const assistantLabel = activeProfile ? labelFor(activeProfile) : 'Relay';
  const channelLabel = activeProfile ? (activeProfile.activeChannel || activeProfile.discordChannel || (activeProfile.allowDms ? 'DM' : '')) : '';
  const accentClass = activeProfile?.type === 'Codex'
    ? 'text-emerald-300'
    : activeProfile?.type === 'Claude'
      ? 'text-amber-300'
      : 'text-indigo-300';

  useEffect(() => {
    if (!workspaces.length) {
      setActiveWorkspace('');
      return;
    }
    if (!activeWorkspace || !workspaces.includes(activeWorkspace)) {
      setActiveWorkspace(workspaces[0]);
    }
  }, [activeWorkspace, workspaces]);

  useEffect(() => {
    if (activeProfile && profileKey(activeProfile) !== selectedProfileId) {
      onSelectProfile(profileKey(activeProfile));
    }
  }, [activeProfile, onSelectProfile, selectedProfileId]);

  useEffect(() => {
    sendingRef.current = sending;
  }, [sending]);

  useEffect(() => {
    let cancelled = false;
    const loadHistory = async () => {
      if (!activeProfile) {
        setMessages([]);
        return;
      }
      setLoading(true);
      try {
        const payload = await api.chatHistory(activeProfile.id, activeProfile.relayType);
        if (!cancelled) {
          const nextMessages = payload.messages || [];
          setMessages((current) => (sendingRef.current ? mergePendingChatMessages(nextMessages, current) : nextMessages));
          setHistoryError('');
        }
      } catch (error) {
        if (!cancelled) {
          setHistoryError(error instanceof Error ? error.message : 'Failed to load local chat history.');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    void loadHistory();
    const interval = window.setInterval(() => void loadHistory(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeProfile]);

  // Track whether the user is near the bottom of the scroll. Discord/Slack
  // pattern: only auto-scroll on new message if the user is already pinned
  // to bottom; otherwise show a "↓ N new" pill.
  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const handleScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      const nearBottom = distance < 96;
      wasNearBottomRef.current = nearBottom;
      if (nearBottom) setUnreadBelow(0);
    };
    node.addEventListener('scroll', handleScroll, { passive: true });
    return () => node.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const profileKeyValue = activeProfile ? profileKey(activeProfile) : null;
    const profileChanged = previousProfileIdRef.current !== profileKeyValue;
    const messageCountChanged = previousMessageCountRef.current !== messages.length;
    if (profileChanged) {
      node.scrollTop = node.scrollHeight;
      wasNearBottomRef.current = true;
      setUnreadBelow(0);
    } else if (messageCountChanged) {
      if (wasNearBottomRef.current) {
        node.scrollTop = node.scrollHeight;
      } else {
        const delta = messages.length - previousMessageCountRef.current;
        if (delta > 0) setUnreadBelow((current) => current + delta);
      }
    }
    previousProfileIdRef.current = profileKeyValue;
    previousMessageCountRef.current = messages.length;
  }, [activeProfile, messages.length]);

  // Auto-grow composer up to ~7 lines.
  useEffect(() => {
    const ta = composerRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    const lineHeight = 22;
    const maxHeight = lineHeight * 7 + 16;
    const next = Math.min(Math.max(ta.scrollHeight, 44), maxHeight);
    ta.style.height = `${next}px`;
  }, [draft]);

  function jumpToLatest() {
    const node = scrollRef.current;
    if (!node) return;
    if (reduceMotion) {
      node.scrollTop = node.scrollHeight;
    } else {
      node.scrollTo({ top: node.scrollHeight, behavior: 'smooth' });
    }
    wasNearBottomRef.current = true;
    setUnreadBelow(0);
  }

  async function sendMessage() {
    if (!activeProfile || !activeProfileCanChat || !draft.trim() || sending) {
      return;
    }
    const content = draft.trim();
    setDraft('');
    setSending(true);
    setMessages((current) => [
      ...current,
      {
        id: `local-${Date.now()}`,
        role: 'user',
        content,
        channelId: activeProfile.activeChannel || activeProfile.discordChannel,
        senderName: 'Operator',
        timestamp: new Date().toISOString(),
      },
    ]);
    try {
      const payload = await api.sendChat(activeProfile.id, activeProfile.relayType, {
        message: content,
        channelId: activeProfile.activeChannel || activeProfile.discordChannel,
        senderName: 'Operator',
        senderId: '0',
      });
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: payload.reply || 'No reply returned from the relay.',
          channelId: payload.channelId || activeProfile.activeChannel || activeProfile.discordChannel,
          senderName: assistantLabel,
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: `error-${Date.now()}`,
          role: 'assistant',
          content: error instanceof Error ? error.message : 'Failed to send local operator message.',
          channelId: activeProfile.activeChannel || activeProfile.discordChannel,
          senderName: assistantLabel,
          timestamp: new Date().toISOString(),
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  const composerDisabled = !activeProfile || !activeProfileCanChat || sending;
  const composerPlaceholder = activeProfile
    ? activeProfileCanChat
      ? `Message ${assistantLabel}${channelLabel ? ` — posts to ${channelLabel.startsWith('#') ? channelLabel : '#' + channelLabel}` : ''}`
      : 'Start this relay before sending a local operator message.'
    : 'Select a relay first.';

  const grouped = chatGroupMessages(messages, assistantLabel);

  // Grid columns: in simple mode the inspector is hidden so the conversation
  // gets the full remaining width.
  const gridCols = isAdvanced
    ? 'xl:grid-cols-[260px_minmax(0,1fr)_300px]'
    : 'xl:grid-cols-[240px_minmax(0,1fr)]';

  return (
    <div className="mx-auto flex w-full max-w-[1640px] flex-1 flex-col px-3 pb-6 pt-4 sm:px-6 sm:pt-6">
      <div className="flex min-h-0 flex-1 overflow-hidden rounded-[24px] border border-white/10 bg-[#0d0d10] shadow-[0_30px_80px_rgba(0,0,0,0.55)]">
        <div className={`grid h-full min-h-0 w-full grid-cols-1 ${gridCols}`}>
          {/* ── Channel sidebar (Discord-style) ───────────────────── */}
          <aside className="flex min-h-0 flex-col border-b border-white/8 bg-[#0a0a0c] xl:border-b-0 xl:border-r">
            <div className="flex items-center justify-between border-b border-white/8 px-4 py-3.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Channels</div>
              <span className="rounded-full bg-white/5 px-2 py-0.5 font-mono text-[10px] text-gray-400">{workspaceProfiles.length}</span>
            </div>
            {workspaces.length > 1 ? (
              <div className="border-b border-white/5 px-3 py-2">
                <select
                  value={activeWorkspace}
                  onChange={(event) => setActiveWorkspace(event.target.value)}
                  className="w-full rounded-lg border border-white/8 bg-white/[0.03] px-2 py-1.5 text-xs text-gray-300 outline-none focus:border-indigo-400/60"
                  aria-label="Workspace"
                >
                  {workspaces.map((workspace) => (
                    <option key={workspace} value={workspace} className="bg-[#0a0a0c]">
                      {workspace.split(/[\\/]/).filter(Boolean).pop() || workspace}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
            <div className="flex-1 overflow-y-auto px-2 py-2" role="listbox" aria-label="Relays in this workspace">
              {workspaceProfiles.length === 0 ? (
                <div className="m-2 rounded-lg border border-dashed border-white/10 px-3 py-6 text-center text-xs text-gray-500">
                  No relays in this workspace yet.
                </div>
              ) : workspaceProfiles.map((profile) => {
                const isActive = activeProfile && profileKey(activeProfile) === profileKey(profile);
                const live = profile.running && profile.ready;
                const dotColor = live
                  ? (profile.type === 'Claude' ? 'bg-amber-400' : 'bg-emerald-400')
                  : profile.running
                    ? 'bg-amber-400/60'
                    : 'bg-slate-600';
                return (
                  <button
                    key={profileKey(profile)}
                    role="option"
                    aria-selected={Boolean(isActive)}
                    onClick={() => onSelectProfile(profileKey(profile))}
                    className={`group flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${isActive ? 'bg-white/8 text-white' : 'text-gray-400 hover:bg-white/[0.04] hover:text-gray-200'}`}
                    title={labelFor(profile)}
                  >
                    <Hash size={15} className={isActive ? accentClass : 'text-gray-500 group-hover:text-gray-300'} />
                    <span className="flex-1 truncate text-sm font-medium">{labelFor(profile).toLowerCase()}</span>
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} aria-hidden />
                  </button>
                );
              })}
            </div>
            {isAdvanced && activeProfile ? (
              <div className="border-t border-white/8 px-4 py-3 text-[10px] font-mono text-gray-500">
                <div className="mb-1 font-semibold uppercase tracking-[0.18em] text-gray-600">Workspace</div>
                <div className="truncate text-gray-400" title={workspaceFor(activeProfile)}>{workspaceFor(activeProfile)}</div>
              </div>
            ) : null}
          </aside>

          {/* ── Conversation column ──────────────────────────────── */}
          <section className="relative flex min-h-0 flex-col bg-[#0d0d10]">
            {/* Header — Discord-style */}
            <header className="flex items-center justify-between gap-4 border-b border-white/8 bg-[#0d0d10] px-4 py-3 sm:px-5">
              {activeProfile ? (
                <>
                  <div className="flex min-w-0 items-center gap-2.5">
                    <Hash size={18} className={accentClass} />
                    <h3 className="truncate text-[15px] font-semibold text-white" title={assistantLabel}>{assistantLabel.toLowerCase()}</h3>
                    <ChatStatusPill status={relayStatus} />
                    {channelLabel ? (
                      <span className="hidden border-l border-white/10 pl-3 font-mono text-[11px] text-gray-500 sm:inline-flex sm:items-center sm:gap-1">
                        <span className="text-gray-600">posts to</span>
                        <span className="text-gray-400">#{channelLabel.replace(/^#/, '')}</span>
                      </span>
                    ) : null}
                  </div>
                  <div className="hidden items-center gap-2 text-xs text-gray-500 sm:flex">
                    <span className="font-mono">{activeProfile.type === 'Claude' ? 'claude' : 'codex'}</span>
                    {isAdvanced && activeProfile.model ? (
                      <>
                        <span className="text-gray-700">·</span>
                        <span className="font-mono">{activeProfile.model}</span>
                      </>
                    ) : null}
                  </div>
                </>
              ) : (
                <div className="text-sm text-gray-500">Select a relay on the left to start chatting.</div>
              )}
            </header>

            {/* Message log */}
            <div
              ref={scrollRef}
              role="log"
              aria-live="polite"
              aria-label={`Conversation with ${assistantLabel}`}
              className="relative flex-1 overflow-y-auto bg-[#0d0d10]"
              style={{ minHeight: '320px' }}
            >
              {!activeProfile ? (
                <div className="flex h-full items-center justify-center px-6 text-center text-sm text-gray-500">
                  Pick a relay on the left to inspect its feed.
                </div>
              ) : loading && !messages.length ? (
                <div className="flex h-full items-center justify-center gap-2 text-sm text-gray-500">
                  <Loader2 size={14} className="animate-spin" />
                  Loading conversation...
                </div>
              ) : historyError ? (
                <div className="flex h-full items-center justify-center px-6 text-center">
                  <div className="max-w-md">
                    <div className="text-sm font-semibold text-red-300">Could not load chat history.</div>
                    <div className="mt-1 text-xs text-gray-500">{historyError}</div>
                  </div>
                </div>
              ) : grouped.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center px-6 text-center">
                  <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/8 bg-white/[0.03]">
                    <Hash size={22} className={accentClass} />
                  </div>
                  <h4 className="mt-4 text-lg font-bold text-white">Welcome to #{assistantLabel.toLowerCase()}</h4>
                  <p className="mt-2 max-w-md text-sm leading-relaxed text-gray-400">
                    This is the start of your conversation with <span className="text-gray-200">{assistantLabel}</span>.
                    Anything you send here goes through the same {activeProfile.type === 'Claude' ? 'Claude' : 'Codex'} relay your Discord users see — minus the round trip.
                  </p>
                  {!activeProfileCanChat ? (
                    <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-amber-400/30 bg-amber-500/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-amber-200">
                      <AlertTriangle size={11} />
                      Relay offline — start it first
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="flex flex-col py-3">
                  {grouped.map((day) => (
                    <div key={day.key} className="flex flex-col">
                      {/* Day separator (full-bleed thin rule with date pill) */}
                      <div className="sticky top-0 z-10 flex items-center gap-3 bg-gradient-to-b from-[#0d0d10] via-[#0d0d10]/95 to-transparent px-4 pb-2 pt-3 sm:px-6">
                        <div className="h-px flex-1 bg-white/8" />
                        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-gray-500">{day.label}</span>
                        <div className="h-px flex-1 bg-white/8" />
                      </div>
                      {day.groups.map((group) => {
                        const initials = chatSenderInitials(group.senderName, group.role);
                        const senderColor = group.role === 'user'
                          ? 'text-indigo-300'
                          : activeProfile.type === 'Codex'
                            ? 'text-emerald-300'
                            : 'text-amber-300';
                        return (
                          <div
                            key={group.key}
                            className="group/row flex items-start gap-3 px-4 py-1.5 transition-colors hover:bg-white/[0.025] sm:px-6"
                          >
                            <div className="w-10 shrink-0 pt-0.5">
                              <ChatAvatar
                                initials={initials}
                                role={group.role}
                                relayType={activeProfile.relayType}
                              />
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-baseline gap-2">
                                <span className={`text-[14px] font-semibold ${senderColor}`}>{group.senderName}</span>
                                <span className="font-mono text-[10px] text-gray-600" title={group.firstTimestamp}>{chatRelativeTime(group.firstTimestamp)}</span>
                                <span className="ml-auto hidden font-mono text-[10px] text-gray-700 group-hover/row:inline" title={group.firstTimestamp}>{chatTimeLabel(group.firstTimestamp)}</span>
                              </div>
                              <div className="mt-0.5 flex flex-col gap-0.5">
                                {group.messages.map((message) => {
                                  const isErrorMessage = message.id.startsWith('error-');
                                  return (
                                    <motion.div
                                      key={message.id}
                                      initial={reduceMotion ? false : { opacity: 0, y: 4 }}
                                      animate={{ opacity: 1, y: 0 }}
                                      transition={{ duration: reduceMotion ? 0 : 0.16, ease: 'easeOut' }}
                                      className={`text-[14px] leading-[1.55] ${isErrorMessage ? 'rounded-md border border-red-500/30 bg-red-500/8 px-3 py-2 text-red-200' : 'text-gray-200'}`}
                                    >
                                      {chatRenderMessageBody(message.content)}
                                    </motion.div>
                                  );
                                })}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                  {sending ? (
                    <div className="flex items-start gap-3 px-4 py-2 sm:px-6">
                      <div className="w-10 shrink-0">
                        <ChatAvatar
                          initials={chatSenderInitials(assistantLabel, 'assistant')}
                          role="assistant"
                          relayType={activeProfile.relayType}
                        />
                      </div>
                      <div className="flex items-center gap-2 pt-2 text-[12px] text-gray-500">
                        <ChatTypingDots accentClass={accentClass} />
                        <span className="font-mono text-[10px] uppercase tracking-[0.18em]">{assistantLabel} is typing</span>
                      </div>
                    </div>
                  ) : null}
                  <div className="h-3" />
                </div>
              )}

              {/* Floating jump-to-latest pill */}
              <AnimatePresence>
                {unreadBelow > 0 ? (
                  <motion.button
                    key="jump-to-latest"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 8 }}
                    transition={{ duration: 0.18, ease: 'easeOut' }}
                    onClick={jumpToLatest}
                    className="absolute bottom-4 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-indigo-400/40 bg-indigo-500/15 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-indigo-200 shadow-[0_8px_24px_rgba(0,0,0,0.5)] backdrop-blur hover:bg-indigo-500/25"
                    aria-label="Jump to latest message"
                  >
                    <ChevronDown size={13} />
                    {unreadBelow} new
                  </motion.button>
                ) : null}
              </AnimatePresence>
            </div>

            {/* Composer — Slack-style: contained pill with embedded send */}
            <div className="border-t border-white/8 bg-[#0d0d10] px-3 py-3 sm:px-5">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void sendMessage();
                }}
                className={`group relative rounded-xl border bg-white/[0.04] transition-colors ${composerDisabled ? 'border-white/8' : 'border-white/12 focus-within:border-indigo-400/50 focus-within:bg-white/[0.06]'}`}
              >
                <div className="flex items-end gap-2 px-3 py-2.5">
                  <textarea
                    ref={composerRef}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault();
                        void sendMessage();
                      }
                    }}
                    placeholder={composerPlaceholder}
                    disabled={composerDisabled}
                    rows={1}
                    className="block max-h-[170px] flex-1 resize-none border-0 bg-transparent text-[14px] leading-[1.5] text-white outline-none placeholder:text-gray-600 disabled:cursor-not-allowed"
                    aria-label="Message composer"
                  />
                  <button
                    type="submit"
                    disabled={composerDisabled || !draft.trim()}
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-[0_4px_14px_rgba(79,70,229,0.4)] transition-all hover:bg-indigo-500 active:translate-y-[1px] disabled:cursor-not-allowed disabled:bg-white/8 disabled:text-gray-600 disabled:shadow-none"
                    aria-label="Send message"
                    title="Send message (Enter)"
                  >
                    {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                  </button>
                </div>
              </form>
              <div className="mt-1.5 flex items-center justify-between px-1 font-mono text-[10px] uppercase tracking-[0.18em] text-gray-600">
                <span className="inline-flex items-center gap-1.5">
                  <CornerDownLeft size={10} />
                  Enter to send · Shift+Enter for newline
                </span>
                {draft.length > 280 ? (
                  <span className={draft.length > 1800 ? 'text-amber-300' : 'text-gray-600'}>{draft.length} chars</span>
                ) : null}
              </div>
            </div>
          </section>

          {/* ── Inspector (advanced view only) ───────────────────── */}
          {isAdvanced ? (
            <aside className="flex min-h-0 flex-col border-l border-white/8 bg-[#0a0a0c]">
              <div className="border-b border-white/8 px-4 py-3.5">
                <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Relay details</div>
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-4">
                {activeProfile ? (
                  <div className="space-y-4">
                    <InspectorRow label="Relay" value={labelFor(activeProfile)} />
                    <InspectorRow label="Workspace" value={workspaceFor(activeProfile)} />
                    <InspectorRow label="Worktree" value={activeProfile.activeWorktree || activeProfile.workspace} mono />
                    <InspectorRow label={activeProfile.type === 'Codex' ? 'Codex home' : 'Claude config'} value={accountHomeFor(activeProfile)} mono />
                    <InspectorRow label="Backend" value={activeProfile.provider || 'Runtime'} />
                    <InspectorRow label="Model" value={activeProfile.model || (activeProfile.type === 'Codex' ? 'Codex default' : 'Claude default')} mono />
                    <InspectorRow label="Effort" value={activeProfile.effort || (activeProfile.type === 'Claude' ? 'Adaptive prompt policy' : 'Adaptive relay policy')} />
                    <InspectorRow label="Trigger" value={activeProfile.triggerMode || 'Mention or direct message'} />
                    <InspectorRow label="Direct messages" value={activeProfile.allowDms ? 'Enabled' : 'Disabled'} />
                    <InspectorRow label="Channel" value={channelFor(activeProfile)} />
                    <InspectorRow label="Current detail" value={activeProfile.statusText || 'No detailed runtime note yet.'} />
                  </div>
                ) : (
                  <div className="text-sm text-gray-500">No relay selected.</div>
                )}
              </div>
            </aside>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function AddProfileModal({ onClose, onSubmit, viewMode = 'simple' }: { onClose: () => void; onSubmit: (data: ProfileFormData) => Promise<void>; viewMode?: ViewMode }) {
  const [type, setType] = useState<ProfileType>('Claude');
  const [name, setName] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [discordToken, setDiscordToken] = useState('');
  const [channelId, setChannelId] = useState('');
  const [model, setModel] = useState('');
  const [codexHome, setCodexHome] = useState('');
  const [claudeConfigDir, setClaudeConfigDir] = useState('');
  const [triggerMode, setTriggerMode] = useState('mention_or_dm');
  const [allowDms, setAllowDms] = useState(false);
  const [operatorIds, setOperatorIds] = useState('');
  const [allowedUserIds, setAllowedUserIds] = useState('');
  const [allowedBotIds, setAllowedBotIds] = useState('');
  const [allowedChannelAuthorIds, setAllowedChannelAuthorIds] = useState('');
  const [channelNoMentionAuthorIds, setChannelNoMentionAuthorIds] = useState('');
  const [channelHistoryLimit, setChannelHistoryLimit] = useState('20');
  const [startupDmUserIds, setStartupDmUserIds] = useState('');
  const [startupDmText, setStartupDmText] = useState('Discord relay online. DM me here to chat with Codex.');
  const [startupChannelText, setStartupChannelText] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(viewMode === 'advanced');
  const [saving, setSaving] = useState(false);
  const [submitError, setSubmitError] = useState('');

  // Simple mode default-collapses the access/startup advanced fields.
  // Operators can still expand them with the toggle below.
  useEffect(() => {
    setShowAdvanced(viewMode === 'advanced');
  }, [viewMode]);

  const codex = type === 'Codex';
  const accessError = profileCreateAccessError(type, channelId, allowDms, operatorIds, allowedUserIds);
  const canSave = Boolean(name.trim() && workspace.trim() && discordToken.trim() && !accessError);

  return (
    <ModalShell title="Add Relay" onClose={onClose} wide>
      <div className="space-y-6">
        {/* Choose engine ------------------------------------------------ */}
        <FormSection
          title="1. Pick the AI engine"
          description="Each relay is backed by either Claude Code (Anthropic) or Codex (OpenAI). Pick whichever you have a CLI logged into on this machine."
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4">
            <TypeButton active={type === 'Claude'} label="Claude Code" icon={<Bot size={18} />} onClick={() => setType('Claude')} tone="orange" />
            <TypeButton active={type === 'Codex'} label="Codex" icon={<Terminal size={18} />} onClick={() => setType('Codex')} tone="emerald" />
          </div>
        </FormSection>

        {/* Identity + Workspace ---------------------------------------- */}
        <FormSection
          title="2. Identity & workspace"
          description="What this relay is called inside CLADEX, and which folder on this machine the AI gets full read/write access to."
        >
          <FormInput
            label="Relay name"
            value={name}
            onChange={setName}
            placeholder="ghostlink"
            helper="Short label shown everywhere in CLADEX (sidebar, status bar, slash commands). Lowercase letters, numbers, and dashes work best."
          />
          <BrowseField
            label="Workspace folder"
            value={workspace}
            onChange={setWorkspace}
            placeholder="C:\\Projects\\my-repo"
            helper="Local folder this relay can read and write. Treat it as the AI's 'project directory' — it'll run commands and edit files inside this folder."
          />
        </FormSection>

        {/* Discord ----------------------------------------------------- */}
        <FormSection
          title="3. Discord connection"
          description="The bot account this relay logs in as, and which channel it listens in."
        >
          <FormInput
            label="Discord bot token"
            value={discordToken}
            onChange={setDiscordToken}
            placeholder="MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMg.ABCDEF.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            type="password"
            helper="From discord.com/developers → your application → Bot tab → Reset Token. Stored encrypted on this machine via Windows DPAPI — never written in plain text."
          />
          <FormInput
            label="Discord channel ID"
            value={channelId}
            onChange={setChannelId}
            placeholder="123456789012345678"
            mono
            helper="Right-click the Discord channel → Copy Channel ID (you must enable Developer Mode in Discord → Settings → Advanced first). Comma-separate to listen in multiple channels."
          />
          <FormInput
            label={`Model (optional)`}
            value={model}
            onChange={setModel}
            placeholder={codex ? 'Leave blank for Codex default (gpt-5-codex)' : 'Leave blank for Claude default (claude-sonnet-4-5)'}
            mono
            helper={codex
              ? 'Override the Codex CLI model for this relay only. Examples: gpt-5-codex, o3-mini. Leave blank to use whatever your Codex CLI defaults to.'
              : 'Override the Claude Code model for this relay only. Examples: claude-sonnet-4-5, claude-opus-4-1. Leave blank to use whatever your Claude CLI defaults to.'}
          />
        </FormSection>

        {/* Optional account home (only show in advanced or when toggled) */}
        {showAdvanced ? (
          <FormSection
            title={codex ? '4. Codex account (advanced)' : '4. Claude account (advanced)'}
            description={codex
              ? 'By default this relay uses your default Codex login. Point CODEX_HOME at a different folder if you want this relay to use a separate Codex account (e.g. a dedicated bot account).'
              : 'By default this relay uses your default Claude login. Point CLAUDE_CONFIG_DIR at a different folder if you want this relay to use a separate Claude account.'}
          >
            {codex ? (
              <BrowseField
                label="Codex account folder"
                value={codexHome}
                onChange={setCodexHome}
                placeholder="C:\\Users\\you\\.codex-bot-account"
                helper="Folder containing a Codex CLI auth.json. Leave blank to share your default Codex login."
              />
            ) : (
              <BrowseField
                label="Claude config folder"
                value={claudeConfigDir}
                onChange={setClaudeConfigDir}
                placeholder="C:\\Users\\you\\.claude-bot-account"
                helper="Folder containing a Claude Code login. Leave blank to share your default Claude login."
              />
            )}
          </FormSection>
        ) : null}

        {/* Who can talk to this bot ----------------------------------- */}
        <FormSection
          title={`${showAdvanced ? '5' : '4'}. Who can talk to this bot`}
          description="Lock the bot down to specific Discord users, or open it up to your whole server. The defaults are sensible — you only need to fill these in if you want tighter control."
        >
          <FormSelect
            label="When should the bot reply?"
            value={triggerMode}
            onChange={setTriggerMode}
            options={[
              { value: 'mention_or_dm', label: 'When @mentioned or DMed (recommended)' },
              { value: 'all', label: 'On every message in the channel' },
              { value: 'dm_only', label: 'Only in direct messages' },
            ]}
            helper="'When @mentioned or DMed' is the safest default. 'On every message' makes the bot reply to anything posted in the channel — useful for dedicated bot channels."
          />

          <ToggleRow
            checked={allowDms}
            onChange={setAllowDms}
            label="Let approved users DM the bot privately"
            helper="When on, users in the 'Discord users allowed to DM' list can chat with the bot in their Discord DMs (private 1-on-1 conversation, no channel needed)."
          />

          {allowDms ? (
            <FormInput
              label="Discord users allowed to DM"
              value={allowedUserIds}
              onChange={setAllowedUserIds}
              placeholder="321521483237031946, 147643658866470402"
              mono
              helper="Comma-separated Discord user IDs. Right-click a user in Discord → Copy User ID. Anyone in this list can DM the bot one-on-one."
            />
          ) : null}

          {showAdvanced ? (
            <>
              <FormInput
                label="Bot operators (admin powers)"
                value={operatorIds}
                onChange={setOperatorIds}
                placeholder="321521483237031946"
                mono
                helper="Comma-separated Discord user IDs. Operators can use admin slash commands like /reset, /interrupt, /steer. Leave blank if no one needs admin access."
              />
              <div className="grid gap-4 md:grid-cols-2">
                <FormInput
                  label="Channel context size"
                  value={channelHistoryLimit}
                  onChange={setChannelHistoryLimit}
                  placeholder="20"
                  mono
                  helper="How many recent channel messages the bot reads as context before replying. Default 20. Set to 0 to disable channel context entirely (each reply is independent)."
                />
                <FormInput
                  label="Other bots allowed to talk"
                  value={allowedBotIds}
                  onChange={setAllowedBotIds}
                  placeholder="(usually empty)"
                  mono
                  helper="By default the bot ignores other bots so you can't accidentally create a reply loop. Add bot IDs here only if you want this bot to converse with another bot."
                />
              </div>

              {codex ? (
                <>
                  <FormInput
                    label="Restrict channel replies to specific people"
                    value={allowedChannelAuthorIds}
                    onChange={setAllowedChannelAuthorIds}
                    placeholder="(blank = anyone in the channel)"
                    mono
                    helper="Comma-separated Discord user IDs. If blank, anyone in the channel can trigger the bot. Set this if you want only certain people to use the bot in a shared channel."
                  />
                  <FormInput
                    label="People who can chat without @mentioning"
                    value={channelNoMentionAuthorIds}
                    onChange={setChannelNoMentionAuthorIds}
                    placeholder="321521483237031946"
                    mono
                    helper="Comma-separated Discord user IDs. These users get a reply for every message they send in the channel — they don't have to type @bot first. Useful for the channel owner so chat feels natural."
                  />
                </>
              ) : null}
            </>
          ) : null}
        </FormSection>

        {/* Optional startup messages ---------------------------------- */}
        {showAdvanced && codex ? (
          <FormSection
            title="6. Startup messages (optional)"
            description="What the bot sends when it comes online. Leave blank for silent startup."
          >
            <FormInput
              label="Send a startup DM to these users"
              value={startupDmUserIds}
              onChange={setStartupDmUserIds}
              placeholder="321521483237031946"
              mono
              helper="Comma-separated Discord user IDs. Each user gets a DM with the message below when the bot comes online. Useful for 'hey, I'm back online' notifications to the operator."
            />
            <FormInput
              label="Startup DM message"
              value={startupDmText}
              onChange={setStartupDmText}
              placeholder="Discord relay online. DM me here to chat with Codex."
              helper="The body of the DM sent to the users above on startup."
            />
            <FormInput
              label="Channel announcement on startup"
              value={startupChannelText}
              onChange={setStartupChannelText}
              placeholder="(blank = silent startup)"
              helper="Optional message posted in the main channel when the bot comes online. Leave blank if you don't want a notification posted publicly."
            />
          </FormSection>
        ) : null}

        {/* Show / hide advanced toggle */}
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400 transition-colors hover:bg-white/[0.06] hover:text-gray-200"
          >
            {showAdvanced ? 'Hide advanced fields' : 'Show advanced fields (account override, operators, startup messages…)'}
          </button>
        </div>

        {accessError ? (
          <div className="rounded-2xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm font-medium text-amber-800 dark:text-amber-100">
            {accessError}
          </div>
        ) : null}
        {submitError ? (
          <div className="rounded-2xl border border-red-500/25 bg-red-500/10 px-4 py-3 text-sm font-medium text-red-800 dark:text-red-100">
            {submitError}
          </div>
        ) : null}

        <div className="flex flex-col-reverse justify-end gap-3 pt-2 sm:flex-row">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Create relay'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} onClick={async () => {
            if (!canSave) return;
            setSaving(true);
            setSubmitError('');
            try {
              await onSubmit({
                name,
                type,
                workspace,
                discordToken,
                channelId,
                model,
                codexHome: codex ? codexHome : '',
                claudeConfigDir: codex ? '' : claudeConfigDir,
                triggerMode,
                allowDms,
                operatorIds,
                allowedUserIds,
                allowedBotIds,
                allowedChannelAuthorIds,
                channelNoMentionAuthorIds,
                channelHistoryLimit,
                startupDmUserIds,
                startupDmText,
                startupChannelText,
              });
            } catch (error) {
              setSubmitError(error instanceof Error ? error.message : 'Failed to save relay.');
            } finally {
              setSaving(false);
            }
          }} busy={saving} disabled={!canSave} />
        </div>
      </div>
    </ModalShell>
  );
}

function EditProfileModal({ profile, onClose, onSubmit, viewMode = 'simple' }: { profile: Profile; onClose: () => void; onSubmit: (data: ProfileSettingsData) => Promise<void>; viewMode?: ViewMode }) {
  const [workspace, setWorkspace] = useState(profile.workspace);
  const [discordToken, setDiscordToken] = useState('');
  const [botName, setBotName] = useState(profile.botName || profile.displayName || '');
  const [model, setModel] = useState(profile.model || '');
  const [codexHome, setCodexHome] = useState(profile.codexHome || '');
  const [claudeConfigDir, setClaudeConfigDir] = useState(profile.claudeConfigDir || '');
  const [triggerMode, setTriggerMode] = useState(profile.triggerMode || 'mention_or_dm');
  const [allowDms, setAllowDms] = useState(Boolean(profile.allowDms));
  const [channelId, setChannelId] = useState(profile.allowedChannelIds || profile.discordChannel || '');
  const [operatorIds, setOperatorIds] = useState(profile.operatorIds || '');
  const [allowedUserIds, setAllowedUserIds] = useState(profile.allowedUserIds || '');
  const [allowedBotIds, setAllowedBotIds] = useState(profile.allowedBotIds || '');
  const [allowedChannelAuthorIds, setAllowedChannelAuthorIds] = useState(profile.allowedChannelAuthorIds || '');
  const [channelNoMentionAuthorIds, setChannelNoMentionAuthorIds] = useState(profile.channelNoMentionAuthorIds || '');
  const [channelHistoryLimit, setChannelHistoryLimit] = useState(profile.channelHistoryLimit || '20');
  const [startupDmUserIds, setStartupDmUserIds] = useState(profile.startupDmUserIds || '');
  const [startupDmText, setStartupDmText] = useState(profile.startupDmText || '');
  const [startupChannelText, setStartupChannelText] = useState(profile.startupChannelText || '');
  const [showAdvanced, setShowAdvanced] = useState(viewMode === 'advanced');
  const [saving, setSaving] = useState(false);
  const [submitError, setSubmitError] = useState('');

  useEffect(() => {
    setShowAdvanced(viewMode === 'advanced');
  }, [viewMode]);

  const codex = profile.type === 'Codex';

  return (
    <ModalShell title={`Edit ${labelFor(profile)}`} onClose={onClose} wide>
      <div className="space-y-6">
        <FormSection
          title="Identity & workspace"
          description="What this relay is called inside CLADEX, and which folder on this machine the AI works in."
        >
          <InspectorRow label="AI engine" value={profile.type} />
          <FormInput
            label="Relay name"
            value={botName}
            onChange={setBotName}
            placeholder="ghostlink"
            helper="Short label shown everywhere in CLADEX (sidebar, status bar, slash commands)."
          />
          <BrowseField
            label="Workspace folder"
            value={workspace}
            onChange={setWorkspace}
            placeholder="C:\\Projects\\my-repo"
            helper="Local folder this relay can read and write. Treat it as the AI's working directory."
          />
        </FormSection>

        <FormSection
          title="Discord connection"
          description="The bot account this relay logs in as, and which channel it listens in."
        >
          <FormInput
            label="Replace Discord bot token"
            value={discordToken}
            onChange={setDiscordToken}
            placeholder="Leave blank to keep the current token"
            type="password"
            helper="Only paste a new token if you regenerated it in the Discord Developer Portal. Stored encrypted via Windows DPAPI."
          />
          <FormInput
            label="Discord channel ID"
            value={channelId}
            onChange={setChannelId}
            placeholder="123456789012345678"
            mono
            helper="Right-click the channel → Copy Channel ID. Comma-separate to listen in multiple channels."
          />
          <FormInput
            label="Model (optional)"
            value={model}
            onChange={setModel}
            placeholder={codex ? 'Leave blank for Codex default (gpt-5-codex)' : 'Leave blank for Claude default (claude-sonnet-4-5)'}
            mono
            helper={codex
              ? 'Override the Codex CLI model for this relay only. Leave blank to use whatever your Codex CLI defaults to.'
              : 'Override the Claude Code model for this relay only. Leave blank to use whatever your Claude CLI defaults to.'}
          />
        </FormSection>

        {showAdvanced ? (
          <FormSection
            title={codex ? 'Codex account (advanced)' : 'Claude account (advanced)'}
            description={codex
              ? 'Point CODEX_HOME at a different folder if you want this relay to use a separate Codex account.'
              : 'Point CLAUDE_CONFIG_DIR at a different folder if you want this relay to use a separate Claude account.'}
          >
            {codex ? (
              <BrowseField
                label="Codex account folder"
                value={codexHome}
                onChange={setCodexHome}
                placeholder="C:\\Users\\you\\.codex-bot-account"
                helper="Folder containing a Codex CLI auth.json. Leave blank to share your default Codex login."
              />
            ) : (
              <BrowseField
                label="Claude config folder"
                value={claudeConfigDir}
                onChange={setClaudeConfigDir}
                placeholder="C:\\Users\\you\\.claude-bot-account"
                helper="Folder containing a Claude Code login. Leave blank to share your default Claude login."
              />
            )}
          </FormSection>
        ) : null}

        <FormSection
          title="Who can talk to this bot"
          description="Lock the bot down to specific Discord users, or open it up to your whole server."
        >
          <FormSelect
            label="When should the bot reply?"
            value={triggerMode}
            onChange={setTriggerMode}
            options={[
              { value: 'mention_or_dm', label: 'When @mentioned or DMed (recommended)' },
              { value: 'all', label: 'On every message in the channel' },
              { value: 'dm_only', label: 'Only in direct messages' },
            ]}
            helper="'When @mentioned or DMed' is the safest default. 'On every message' makes the bot reply to anything posted in the channel."
          />

          <ToggleRow
            checked={allowDms}
            onChange={setAllowDms}
            label="Let approved users DM the bot privately"
            helper="When on, users in the 'Discord users allowed to DM' list can chat with the bot in their Discord DMs (private 1-on-1)."
          />

          {allowDms ? (
            <FormInput
              label="Discord users allowed to DM"
              value={allowedUserIds}
              onChange={setAllowedUserIds}
              placeholder="321521483237031946, 147643658866470402"
              mono
              helper="Comma-separated Discord user IDs. Right-click a user in Discord → Copy User ID."
            />
          ) : null}

          {showAdvanced ? (
            <>
              <FormInput
                label="Bot operators (admin powers)"
                value={operatorIds}
                onChange={setOperatorIds}
                placeholder="321521483237031946"
                mono
                helper="Comma-separated Discord user IDs. Operators can use admin slash commands like /reset, /interrupt, /steer."
              />
              <div className="grid gap-4 md:grid-cols-2">
                <FormInput
                  label="Channel context size"
                  value={channelHistoryLimit}
                  onChange={setChannelHistoryLimit}
                  placeholder="20"
                  mono
                  helper="How many recent channel messages the bot reads as context before replying. Default 20. Set to 0 to disable."
                />
                <FormInput
                  label="Other bots allowed to talk"
                  value={allowedBotIds}
                  onChange={setAllowedBotIds}
                  placeholder="(usually empty)"
                  mono
                  helper="By default the bot ignores other bots so you can't accidentally create a reply loop. Add bot IDs here only if you want bot-to-bot chat."
                />
              </div>
              {codex ? (
                <>
                  <FormInput
                    label="Restrict channel replies to specific people"
                    value={allowedChannelAuthorIds}
                    onChange={setAllowedChannelAuthorIds}
                    placeholder="(blank = anyone in the channel)"
                    mono
                    helper="If blank, anyone in the channel can trigger the bot. Set this if you want only certain people to use the bot."
                  />
                  <FormInput
                    label="People who can chat without @mentioning"
                    value={channelNoMentionAuthorIds}
                    onChange={setChannelNoMentionAuthorIds}
                    placeholder="321521483237031946"
                    mono
                    helper="These users get a reply for every message they send in the channel — they don't have to type @bot first."
                  />
                </>
              ) : null}
            </>
          ) : null}
        </FormSection>

        {showAdvanced && codex ? (
          <FormSection
            title="Startup messages (optional)"
            description="What the bot sends when it comes online. Leave blank for silent startup."
          >
            <FormInput
              label="Send a startup DM to these users"
              value={startupDmUserIds}
              onChange={setStartupDmUserIds}
              placeholder="321521483237031946"
              mono
              helper="Comma-separated Discord user IDs. Each user gets a DM with the message below when the bot comes online."
            />
            <FormInput
              label="Startup DM message"
              value={startupDmText}
              onChange={setStartupDmText}
              placeholder="Discord relay online. DM me here to chat with Codex."
              helper="The body of the DM sent to the users above on startup."
            />
            <FormInput
              label="Channel announcement on startup"
              value={startupChannelText}
              onChange={setStartupChannelText}
              placeholder="(blank = silent startup)"
              helper="Optional message posted in the main channel when the bot comes online."
            />
          </FormSection>
        ) : null}

        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400 transition-colors hover:bg-white/[0.06] hover:text-gray-200"
          >
            {showAdvanced ? 'Hide advanced fields' : 'Show advanced fields'}
          </button>
        </div>
        {submitError ? (
          <div className="rounded-2xl border border-red-500/25 bg-red-500/10 px-4 py-3 text-sm font-medium text-red-800 dark:text-red-100">
            {submitError}
          </div>
        ) : null}

        <div className="flex flex-col-reverse justify-end gap-3 pt-2 sm:flex-row">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Save changes'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <Pencil size={16} />} onClick={async () => {
            setSaving(true);
            setSubmitError('');
            try {
              await onSubmit({
                type: profile.type,
                workspace,
                discordToken,
                botName,
                model,
                codexHome: codex ? codexHome : '',
                claudeConfigDir: codex ? '' : claudeConfigDir,
                triggerMode,
                allowDms,
                channelId,
                operatorIds,
                allowedUserIds,
                allowedBotIds,
                allowedChannelAuthorIds,
                channelNoMentionAuthorIds,
                channelHistoryLimit,
                startupDmUserIds,
                startupDmText,
                startupChannelText,
              });
            } catch (error) {
              setSubmitError(error instanceof Error ? error.message : 'Failed to save relay.');
            } finally {
              setSaving(false);
            }
          }} />
        </div>
      </div>
    </ModalShell>
  );
}

function WorkgroupModal({
  profiles,
  onClose,
  onSubmit,
}: {
  profiles: Profile[];
  onClose: () => void;
  onSubmit: (name: string, members: Array<{ id: string; relayType: RelayType }>) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [selectedIds, setSelectedIds] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const selectedMembers = profiles.filter((profile) => selectedIds[profileKey(profile)]).map((profile) => ({ id: profile.id, relayType: profile.relayType }));
  const nameFilled = name.trim().length > 0;
  const canSave = nameFilled && selectedMembers.length > 0;
  const validationMessage = !nameFilled ? 'Name the workgroup before saving.' : selectedMembers.length === 0 ? 'Select at least one relay.' : '';

  return (
    <ModalShell title="Create Workgroup" onClose={onClose} wide>
      <div className="space-y-4">
        <FormInput label="Workgroup name" value={name} onChange={setName} placeholder="Core team" />
        <div className="rounded-2xl border border-white/10 bg-black/30 p-4">
          <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Included relays</div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {profiles.map((profile) => (
              <label key={profileKey(profile)} className="flex items-start gap-3 rounded-2xl border border-white/5 bg-white/[0.03] px-4 py-3">
                <input type="checkbox" checked={Boolean(selectedIds[profileKey(profile)])} onChange={(event) => setSelectedIds((current) => ({ ...current, [profileKey(profile)]: event.target.checked }))} className="mt-1 h-4 w-4 accent-indigo-500" />
                <div>
                  <div className="text-sm font-semibold text-white">{labelFor(profile)}</div>
                  <div className="text-xs text-gray-500">{workspaceFor(profile)} · {profile.type}</div>
                </div>
              </label>
            ))}
          </div>
        </div>
        {submitError || validationMessage ? (
          <div className={`rounded-2xl border px-4 py-3 text-sm font-medium ${submitError ? 'border-red-500/25 bg-red-500/10 text-red-800 dark:text-red-100' : 'border-amber-500/25 bg-amber-500/10 text-amber-800 dark:text-amber-100'}`}>
            {submitError || validationMessage}
          </div>
        ) : null}
        <div className="flex flex-col-reverse justify-end gap-3 pt-2 sm:flex-row">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Save workgroup'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <FolderKanban size={16} />} onClick={async () => {
            if (!canSave) {
              setSubmitError('');
              return;
            }
            setSaving(true);
            setSubmitError('');
            try {
              await onSubmit(name.trim(), selectedMembers);
            } catch (error) {
              setSubmitError(error instanceof Error ? error.message : 'Failed to save workgroup.');
            } finally {
              setSaving(false);
            }
          }} busy={saving} disabled={!canSave} />
        </div>
      </div>
    </ModalShell>
  );
}

function SettingsModal({
  runtimeInfo,
  allowCladexSelfReview,
  onChangeAllowCladexSelfReview,
  onClose,
  onStopAll,
}: {
  runtimeInfo: RuntimeInfo | null;
  allowCladexSelfReview: boolean;
  onChangeAllowCladexSelfReview: (value: boolean) => void;
  onClose: () => void;
  onStopAll: () => void;
}) {
  const handleSelfReviewToggle = (checked: boolean) => {
    if (!checked) {
      onChangeAllowCladexSelfReview(false);
      return;
    }
    if (window.confirm('Allow Review Swarm to target the CLADEX app repo for this app session? This is only for deliberate CLADEX development and should stay off for normal project reviews.')) {
      onChangeAllowCladexSelfReview(true);
    }
  };
  return (
    <ModalShell title="CLADEX Runtime" onClose={onClose} wide>
      <div className="space-y-6">
        <p className="text-sm leading-relaxed text-slate-600 dark:text-gray-400">This panel shows the real runtime state and rare global safety overrides. Day-to-day profile behavior still lives with each relay.</p>
        <InspectorRow label="API base" value={runtimeInfo?.apiBase || 'Loading...'} mono />
        <InspectorRow label="Backend path" value={runtimeInfo?.backendDir || 'Loading...'} mono />
        {runtimeInfo?.frontendDir ? <InspectorRow label="Frontend path" value={runtimeInfo.frontendDir} mono /> : null}
        <InspectorRow label="App version" value={runtimeInfo?.appVersion || 'Loading...'} />
        <InspectorRow label="Packaging" value={runtimeInfo?.packaged ? 'Packaged desktop build' : 'Source build'} />
        {runtimeInfo?.remoteAccessToken ? <InspectorRow label="Remote token" value={maskSecret(runtimeInfo.remoteAccessToken)} mono /> : null}
        <div className="rounded-2xl border border-slate-200/80 bg-white/70 p-4 text-sm text-slate-600 dark:border-white/10 dark:bg-black/30 dark:text-gray-400">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Runtime notes</div>
          <ul className="space-y-2">
            <li>Codex stays the deeper runtime because it is app-server based.</li>
            <li>Claude now shares the same durable memory, worktree, status, and handoff path instead of a thin side path.</li>
            <li>Bot labels, trigger mode, model choice, and DM access are managed per relay profile.</li>
          </ul>
        </div>
        <div className="rounded-2xl border border-amber-400/30 bg-amber-500/[0.08] p-4 text-sm text-amber-950 dark:text-amber-100">
          <div className="mb-3 text-[10px] font-bold uppercase tracking-[0.22em] text-amber-700 dark:text-amber-300">CLADEX development safety</div>
          <ToggleRow
            checked={allowCladexSelfReview}
            onChange={handleSelfReviewToggle}
            label="Allow Review Swarm to target the CLADEX app repo this session"
          />
          <div className="mt-3 text-xs leading-relaxed text-amber-800 dark:text-amber-200/80">
            Keep this off for normal project reviews. Enable it only when intentionally reviewing CLADEX itself; write-capable self-fix still requires a separate confirmation.
          </div>
        </div>
        <div className="rounded-2xl border border-slate-200/80 bg-white/70 p-4 text-sm text-slate-600 dark:border-white/10 dark:bg-black/30 dark:text-gray-400">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">First Run Checklist</div>
          <ul className="space-y-2">
            <li>CLADEX manages local relays, but it still needs Python 3.10+ on the machine.</li>
            <li>Install `codex` if you want Codex relays, `claude` if you want Claude relays, or both.</li>
            <li>Create a relay profile with a workspace path, Discord bot token, and an allowed channel id or scoped DM allowlist.</li>
            <li>Start the profile from the Relays view and confirm it reaches Ready before testing in Discord.</li>
          </ul>
          <div className="mt-3 text-xs text-slate-500 dark:text-gray-500">
            Packaging: {runtimeInfo?.packaged ? 'This is a packaged desktop build. The bundled backend is included, but Python and the AI CLIs are still external dependencies.' : 'This is a source build. Run from the repo root after installing dependencies.'}
          </div>
        </div>
        <div className="flex flex-col-reverse justify-end gap-3 sm:flex-row">
          <SecondaryButton label="Close" onClick={onClose} />
          <ActionButton label="Stop All" icon={<PauseCircle size={16} />} tone="danger" onClick={onStopAll} />
        </div>
      </div>
    </ModalShell>
  );
}

function LogsModal({ profile, onClose }: { profile: Profile; onClose: () => void }) {
  const [logs, setLogs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorText, setErrorText] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const payload = await api.logs(profile.id, profile.relayType);
        if (!cancelled) {
          setLogs(payload.logs || []);
          setErrorText('');
        }
      } catch (error) {
        if (!cancelled) {
          setErrorText(error instanceof Error ? error.message : 'Failed to load logs.');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    void load();
    const interval = window.setInterval(() => void load(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [profile.id, profile.relayType]);

  // T3.1 / frontend-audit #1: Render `errorText` as a banner ABOVE the
  // log buffer, never as a replacement. A single failed 3-second poll
  // used to wipe the entire scrollback the operator was reading.
  return (
    <ModalShell title={`Live logs · ${labelFor(profile)}`} onClose={onClose} wide>
      {errorText ? (
        <div className="mb-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          {errorText} — last successful logs shown below.
        </div>
      ) : null}
      <div className="h-80 overflow-y-auto rounded-2xl border border-white/5 bg-black p-4 font-mono text-xs text-gray-300">
        {loading && logs.length === 0 ? (
          <div className="flex items-center gap-2 text-indigo-300"><Loader2 size={14} className="animate-spin" /> Loading logs...</div>
        ) : logs.length ? (
          logs.map((line, index) => <div key={`${profile.id}-${index}`}>{line}</div>)
        ) : !errorText ? (
          <div className="text-gray-500">No log lines recorded yet for this relay.</div>
        ) : null}
      </div>
    </ModalShell>
  );
}

function MetaPill({ label, mono = false }: { label: string; mono?: boolean }) {
  return (
    <div className={`rounded-full border border-slate-200/80 bg-white/70 px-3 py-1.5 text-[11px] text-slate-600 dark:border-white/10 dark:bg-white/[0.04] dark:text-gray-300 ${mono ? 'font-mono' : ''}`}>
      {label}
    </div>
  );
}

function EmptyState({ title, detail, compact = false, actionLabel, onAction }: { title: string; detail: string; compact?: boolean; actionLabel?: string; onAction?: () => void }) {
  return <div className={`flex flex-col items-center justify-center rounded-2xl border border-slate-200/80 bg-white/70 px-6 text-center dark:border-white/10 dark:bg-white/[0.03] ${compact ? 'h-48 py-8' : 'h-64 py-12'}`}><Activity size={compact ? 28 : 40} className="mb-4 text-slate-400 dark:text-gray-600" /><div className="text-lg font-semibold text-slate-900 dark:text-white">{title}</div><div className="mt-2 max-w-xl text-sm text-slate-500 dark:text-gray-500">{detail}</div>{actionLabel && onAction ? <button onClick={onAction} className="mt-5 rounded-2xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-500">{actionLabel}</button> : null}</div>;
}

function FirstRunGuide({ packaged, includeTroubleshooting = false }: { packaged: boolean; includeTroubleshooting?: boolean }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
      <div className="rounded-[28px] border border-slate-200/80 bg-white/70 p-5 dark:border-white/10 dark:bg-white/[0.03]">
        <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Requirements</div>
        <div className="mt-3 text-sm text-slate-600 dark:text-gray-400">
          {packaged ? 'The packaged desktop build includes the CLADEX UI and bundled backend files. It does not bundle Python or the external AI CLIs.' : 'The source build expects local development dependencies and the external AI CLIs to already be installed.'}
        </div>
        <ul className="mt-4 space-y-2 text-sm text-slate-700 dark:text-gray-300">
          {FIRST_RUN_REQUIREMENTS.map((item) => <li key={item}>- {item}</li>)}
        </ul>
      </div>
      <div className="rounded-[28px] border border-slate-200/80 bg-white/70 p-5 dark:border-white/10 dark:bg-white/[0.03]">
        <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Get Started</div>
        <ol className="mt-4 space-y-2 text-sm text-slate-700 dark:text-gray-300">
          {FIRST_RUN_STEPS.map((item, index) => <li key={item}>{index + 1}. {item}</li>)}
        </ol>
        {includeTroubleshooting ? (
          <div className="mt-4 rounded-2xl border border-amber-300/30 bg-amber-500/10 px-3 py-3 text-sm text-amber-100">
            If the runtime will not start, the most common cause is missing Python. After that, check whether the `codex` or `claude` command is installed for the relay type you are trying to create.
          </div>
        ) : null}
      </div>
    </div>
  );
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3 py-1"><div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">{label}</div><div className={`text-sm text-slate-700 dark:text-gray-300 ${mono ? 'font-mono' : ''}`}>{value}</div></div>;
}

function InspectorRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="grid grid-cols-[110px_minmax(0,1fr)] gap-3"><div className="pt-1 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">{label}</div><div className={`rounded-2xl border border-slate-200/80 bg-white/80 px-3 py-2 text-sm text-slate-800 dark:border-white/5 dark:bg-black/30 dark:text-gray-200 ${mono ? 'break-all font-mono' : ''}`}>{value}</div></div>;
}

function FormFieldLabel({ label, helper }: { label: string; helper?: string }) {
  return (
    <div className="mb-2">
      <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-gray-400">{label}</div>
      {helper ? (
        <div className="mt-1 text-[12px] leading-relaxed text-slate-500 dark:text-gray-500">{helper}</div>
      ) : null}
    </div>
  );
}

function FormInput({ label, value, onChange, placeholder, mono = false, type = 'text', helper }: { label: string; value: string; onChange: (value: string) => void; placeholder: string; mono?: boolean; type?: string; helper?: string }) {
  return (
    <label className="block">
      <FormFieldLabel label={label} helper={helper} />
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className={`w-full rounded-xl border border-slate-200 bg-white/80 px-3.5 py-2.5 text-slate-900 outline-none focus:border-indigo-500 dark:border-white/10 dark:bg-black/40 dark:text-white ${mono ? 'font-mono text-[13px]' : 'text-sm'}`}
      />
    </label>
  );
}

function BrowseField({ label, value, onChange, onPicked, placeholder, buttonLabel, stacked = false, helper }: { label: string; value: string; onChange: (value: string) => void; onPicked?: (value: string) => void; placeholder: string; buttonLabel?: string; stacked?: boolean; helper?: string }) {
  const [browserOpen, setBrowserOpen] = useState(false);
  const desktopPickerAvailable = Boolean(window.cladexDesktop?.chooseDirectory);
  return (
    <>
    <label className="block">
      <FormFieldLabel label={label} helper={helper} />
      <div className={`flex flex-col gap-3 ${stacked ? '' : 'sm:flex-row'}`}>
        <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white/80 px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-500 dark:border-white/10 dark:bg-black/40 dark:text-white" />
        <button
          type="button"
          onClick={async () => {
            if (desktopPickerAvailable) {
              const chosen = await chooseWorkspaceFolder(value);
              onChange(chosen);
              if (chosen && chosen !== value) {
                onPicked?.(chosen);
              }
              return;
            }
            setBrowserOpen(true);
          }}
          className="rounded-xl border border-slate-200 bg-white/80 px-4 py-2.5 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-100 dark:border-white/10 dark:bg-white/[0.03] dark:text-gray-200 dark:hover:bg-white/[0.08]"
        >
          {buttonLabel || (desktopPickerAvailable ? 'Browse' : 'Browse server')}
        </button>
      </div>
    </label>
    {browserOpen ? <DirectoryBrowserModal initialPath={value} onClose={() => setBrowserOpen(false)} onPick={(nextPath) => { onChange(nextPath); onPicked?.(nextPath); setBrowserOpen(false); }} /> : null}
    </>
  );
}

function FormSection({ title, children, description }: { title: string; children: React.ReactNode; description?: string }) {
  return (
    <section className="space-y-3">
      <div>
        <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-gray-400">{title}</div>
        {description ? <div className="mt-1 text-[12px] leading-relaxed text-slate-500 dark:text-gray-500">{description}</div> : null}
      </div>
      <div className="space-y-4 rounded-2xl border border-slate-200/80 bg-white/60 p-4 dark:border-white/10 dark:bg-black/25">
        {children}
      </div>
    </section>
  );
}

function FormSelect({ label, value, onChange, options, helper }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }>; helper?: string }) {
  return (
    <label className="block">
      <FormFieldLabel label={label} helper={helper} />
      <select value={value} onChange={(event) => onChange(event.target.value)} className="w-full rounded-xl border border-slate-200 bg-white/80 px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-500 dark:border-white/10 dark:bg-black/40 dark:text-white">
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}

function ToggleRow({ checked, onChange, label, helper }: { checked: boolean; onChange: (checked: boolean) => void; label: string; helper?: string }) {
  return (
    <label className="flex items-start gap-3 rounded-xl border border-slate-200/80 bg-white/80 px-4 py-3 text-sm text-slate-700 dark:border-white/10 dark:bg-black/30 dark:text-gray-300">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-0.5 h-4 w-4 accent-emerald-500" />
      <div className="min-w-0">
        <div className="font-medium text-slate-700 dark:text-gray-200">{label}</div>
        {helper ? <div className="mt-1 text-[12px] leading-relaxed text-slate-500 dark:text-gray-500">{helper}</div> : null}
      </div>
    </label>
  );
}

function TypeButton({ active, label, icon, onClick, tone }: { active: boolean; label: string; icon: React.ReactNode; onClick: () => void; tone: 'orange' | 'emerald' }) {
  const activeStyles = tone === 'orange' ? 'border-orange-500/40 bg-orange-500/10 text-orange-800 dark:text-orange-200' : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200';
  return <button type="button" onClick={onClick} className={`flex min-h-12 items-center justify-center gap-3 rounded-[16px] border px-4 py-3 font-semibold transition-colors ${active ? activeStyles : 'border-slate-200/80 bg-white/55 text-slate-500 hover:bg-white dark:border-white/10 dark:bg-white/[0.03] dark:text-gray-400 dark:hover:bg-white/[0.06]'}`}>{icon}{label}</button>;
}

function RemoteAccessModal({
  token,
  onChangeToken,
  onSubmit,
}: {
  token: string;
  onChangeToken: (value: string) => void;
  onSubmit: () => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState('');
  return (
    <ModalShell title="Remote Access Token" onClose={() => undefined}>
      <div className="space-y-4">
        <p className="text-sm leading-relaxed text-slate-600 dark:text-gray-400">
          This CLADEX instance is being opened from a non-local origin. Enter the CLADEX remote access token from the local Runtime panel to continue.
        </p>
        <FormInput label="Access token" value={token} onChange={onChangeToken} placeholder="Paste the CLADEX remote access token" mono type="password" />
        {errorText ? <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-200">{errorText}</div> : null}
        <div className="flex flex-col-reverse justify-end gap-3 sm:flex-row">
          <SecondaryButton label="Clear saved token" onClick={() => { storeAccessToken(''); onChangeToken(''); setErrorText(''); }} />
          <PrimaryButton label={submitting ? 'Connecting...' : 'Unlock CLADEX'} icon={submitting ? <Loader2 size={16} className="animate-spin" /> : <Settings size={16} />} onClick={async () => {
            setSubmitting(true);
            setErrorText('');
            try {
              await onSubmit();
            } catch (error) {
              setErrorText(error instanceof Error ? error.message : 'Could not unlock CLADEX with that token.');
            } finally {
              setSubmitting(false);
            }
          }} />
        </div>
      </div>
    </ModalShell>
  );
}

function DirectoryBrowserModal({
  initialPath,
  onClose,
  onPick,
}: {
  initialPath: string;
  onClose: () => void;
  onPick: (value: string) => void;
}) {
  const [currentPath, setCurrentPath] = useState(initialPath);
  const [currentListing, setCurrentListing] = useState<DirectoryListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorText, setErrorText] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setErrorText('');
      try {
        const listing = await api.listDirectories(currentPath);
        if (!cancelled) {
          setCurrentListing(listing);
          setCurrentPath(listing.currentPath || currentPath);
        }
      } catch (error) {
        if (!cancelled) {
          setErrorText(error instanceof Error ? error.message : 'Failed to load folders.');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [currentPath]);

  return (
    <ModalShell title="Browse Workspace Folder" onClose={onClose} wide>
      <div className="space-y-4">
        <FormInput label="Current path" value={currentPath} onChange={setCurrentPath} placeholder="C:\\Projects" mono />
        {currentListing?.parentPath ? <SecondaryButton label="Up one level" onClick={() => setCurrentPath(currentListing.parentPath)} /> : null}
        <div className="rounded-2xl border border-slate-200/80 bg-white/70 p-3 dark:border-white/10 dark:bg-black/20">
          <div className="mb-3 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Folders</div>
          <div className="max-h-[45vh] space-y-2 overflow-y-auto">
            {loading ? <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-gray-400"><Loader2 size={14} className="animate-spin" /> Loading folders...</div> : null}
            {!loading && errorText ? <div className="text-sm text-amber-600 dark:text-amber-300">{errorText}</div> : null}
            {!loading && !errorText && currentListing?.directories.length === 0 ? <div className="text-sm text-slate-500 dark:text-gray-400">No subfolders here.</div> : null}
            {!loading && !errorText ? currentListing?.directories.map((entry) => (
              <button key={entry.path} type="button" onClick={() => setCurrentPath(entry.path)} className="flex w-full items-center justify-between rounded-2xl border border-slate-200 bg-white/80 px-3 py-3 text-left text-sm text-slate-800 transition-colors hover:bg-slate-100 dark:border-white/10 dark:bg-white/[0.03] dark:text-gray-200 dark:hover:bg-white/[0.08]">
                <span className="truncate">{entry.name}</span>
                <FolderKanban size={16} className="ml-3 shrink-0 text-slate-400 dark:text-gray-500" />
              </button>
            )) : null}
          </div>
        </div>
        <div className="flex flex-col-reverse justify-end gap-3 sm:flex-row">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label="Use this folder" icon={<FolderKanban size={16} />} onClick={() => onPick(currentPath)} />
        </div>
      </div>
    </ModalShell>
  );
}

function ModalShell({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  // T3.3 / frontend-audit #3: Escape dismisses, role="dialog" + aria-modal,
  // close button has aria-label. Without these, modals were undismissible
  // by keyboard and screen readers had no announcement that they were
  // inside a dialog.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[100] flex items-end justify-center overflow-y-auto bg-black/60 p-3 pt-8 backdrop-blur-sm sm:items-start sm:p-6 sm:pt-12"
      onClick={onClose}
    >
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        initial={{ scale: 0.98, y: 8 }}
        animate={{ scale: 1, y: 0 }}
        exit={{ scale: 0.98, y: 8 }}
        transition={{ duration: 0.18, ease: [0.32, 0.72, 0, 1] }}
        onClick={(event) => event.stopPropagation()}
        className={`mb-3 max-h-[calc(100vh-1.5rem)] w-full overflow-hidden rounded-2xl border border-slate-200/80 bg-[#f8f6f0] shadow-[0_28px_80px_rgba(15,23,42,0.22)] sm:mb-12 dark:border-white/10 dark:bg-[#0a0a0c] dark:shadow-2xl ${wide ? 'max-w-2xl' : 'max-w-md'}`}
      >
        <div className="flex items-center justify-between border-b border-slate-200 bg-white/50 px-4 py-4 sm:px-5 dark:border-white/5 dark:bg-white/[0.03]">
          <div>
            <div className="text-[9px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-gray-500">CLADEX</div>
            <div className="mt-0.5 text-lg font-semibold text-slate-900 dark:text-white">{title}</div>
          </div>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={onClose}
            className="rounded-full bg-slate-200/70 p-1.5 text-slate-500 transition-colors hover:bg-slate-300 hover:text-slate-900 dark:bg-white/5 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white"
          >
            <X size={14} />
          </button>
        </div>
        <div className="max-h-[calc(100vh-6.5rem)] overflow-y-auto p-4 sm:p-5">{children}</div>
      </motion.div>
    </motion.div>
  );
}

function DockButton({ icon, label, active, onClick, light = false }: { icon: React.ReactNode; label: string; active?: boolean; onClick: () => void; light?: boolean }) {
  // v3 ui-design pass: the dock magnetism is THE one weird decision — the
  // operator's hand drifts toward a control. Keep it, but tighten:
  // displacement coefficient 0.3 → 0.18 (smaller pull); spring stiffness/
  // damping bumped (snappier, less floaty); whileHover scale 1.08 → 1.04
  // (the button doesn't grow much, it just catches your hand).
  const ref = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  return <div className="group relative"><motion.button type="button" aria-label={label} aria-current={active ? 'page' : undefined} ref={ref} onMouseMove={(event) => { if (!ref.current) return; const bounds = ref.current.getBoundingClientRect(); setPosition({ x: (event.clientX - (bounds.left + bounds.width / 2)) * 0.18, y: (event.clientY - (bounds.top + bounds.height / 2)) * 0.18 }); }} onMouseLeave={() => setPosition({ x: 0, y: 0 })} animate={{ x: position.x, y: position.y }} transition={{ type: 'spring', stiffness: 240, damping: 22, mass: 0.1 }} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.96 }} onClick={onClick} className={`rounded-xl p-3 transition-colors ${active ? 'bg-indigo-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.5)]' : light ? 'text-slate-600 hover:bg-black/5 hover:text-slate-900' : 'text-gray-400 hover:bg-white/10 hover:text-white'}`}>{icon}</motion.button><div className={`pointer-events-none absolute bottom-full left-1/2 mb-3 -translate-x-1/2 whitespace-nowrap rounded-lg border px-3 py-1.5 text-xs font-bold opacity-0 transition-opacity group-hover:opacity-100 ${light ? 'border-slate-200 bg-white text-slate-800 shadow-xl' : 'border-white/10 bg-black/80 text-white'}`}>{label}</div></div>;
}

function ActionButton({ label, icon, onClick, busy = false, disabled = false, tone = 'default', light = false }: { label: string; icon: React.ReactNode; onClick: () => void; busy?: boolean; disabled?: boolean; tone?: 'default' | 'danger'; light?: boolean }) {
  return <button type="button" onClick={onClick} disabled={busy || disabled} className={`inline-flex items-center gap-2 rounded-[14px] border px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${tone === 'danger' ? 'border-red-500/25 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-200' : light ? 'border-slate-300 bg-white text-slate-800 hover:bg-slate-100' : 'border-slate-200/80 bg-white/70 text-slate-800 hover:bg-white dark:border-white/10 dark:bg-white/[0.04] dark:text-white dark:hover:bg-white/[0.08]'}`}>{busy ? <Loader2 size={16} className="animate-spin" /> : icon}{label}</button>;
}

function PrimaryButton({ label, icon, onClick, busy = false, disabled = false }: { label: string; icon: React.ReactNode; onClick: () => void; busy?: boolean; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy || disabled}
      aria-busy={busy || undefined}
      className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-indigo-600"
    >
      {icon}
      {label}
    </button>
  );
}

function SecondaryButton({ label, onClick, busy = false }: { label: string; onClick: () => void; busy?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-busy={busy || undefined}
      className="w-full rounded-2xl border border-slate-200 bg-white/70 px-4 py-2 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-white/70 dark:border-white/10 dark:bg-white/[0.03] dark:text-gray-300 dark:hover:bg-white/[0.08] dark:disabled:hover:bg-white/[0.03]"
    >
      {label}
    </button>
  );
}

function MiniIconButton({ label, icon, onClick, tone = 'default' }: { label: string; icon: React.ReactNode; onClick: () => void; tone?: 'default' | 'danger' }) {
  return <button type="button" title={label} onClick={onClick} className={`inline-flex h-9 w-9 items-center justify-center rounded-full border transition-colors ${tone === 'danger' ? 'border-red-500/20 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-200' : 'border-slate-200/80 bg-white/70 text-slate-500 hover:bg-slate-200 hover:text-slate-900 dark:border-white/5 dark:bg-white/5 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white'}`}>{icon}</button>;
}

function ViewModeToggle({ value, onChange }: { value: ViewMode; onChange: (next: ViewMode) => void }) {
  // Two-state pill: Simple / Advanced. Persists across launches via the
  // `cladex.viewMode` localStorage key (handled in App). Affects every
  // screen — Relays cards, Workgroups, Review Swarm, Live Console, the
  // Add/Edit Profile modal — by hiding seldom-touched fields in Simple
  // and surfacing every knob in Advanced.
  return (
    <div
      role="tablist"
      aria-label="Interface density"
      className="inline-flex h-9 items-center rounded-full border border-white/10 bg-white/[0.04] p-0.5 text-[11px] font-semibold uppercase tracking-[0.16em]"
    >
      {([
        { id: 'simple' as const, label: 'Simple', hint: 'Just the essentials. Hides power-user knobs.' },
        { id: 'advanced' as const, label: 'Advanced', hint: 'Surfaces every option on every screen.' },
      ]).map((option) => {
        const active = value === option.id;
        return (
          <button
            key={option.id}
            type="button"
            role="tab"
            aria-selected={active}
            title={option.hint}
            onClick={() => onChange(option.id)}
            className={`inline-flex h-8 items-center rounded-full px-3 transition-colors ${active ? 'bg-indigo-500/85 text-white shadow-[0_2px_10px_rgba(79,70,229,0.35)]' : 'text-gray-400 hover:text-gray-200'}`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
