import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useMotionTemplate, useMotionValue, useSpring, useTransform } from 'motion/react';
import {
  Activity,
  AlertTriangle,
  Bot,
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
  RefreshCw,
  RotateCcw,
  SearchCheck,
  Settings,
  Square,
  Terminal,
  Trash2,
  Wrench,
  X,
} from 'lucide-react';
import CladexBackground from './components/CladexBackground';

type ViewName = 'relays' | 'workgroups' | 'review' | 'live';
type ProfileType = 'Claude' | 'Codex';
type RelayType = 'claude' | 'codex';
type ReviewProvider = 'codex' | 'claude';
type ReviewJobStatus = 'queued' | 'running' | 'completed' | 'completed_with_warnings' | 'failed' | 'cancelled';
type FixRunStatus = 'queued' | 'running' | 'completed' | 'completed_with_warnings' | 'failed' | 'cancelled';

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
  limitWarnings?: string[];
  warnings?: string[];
  limits?: LimitMetadata;
  error?: string;
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
  if (profile.running) {
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

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  const accessToken = getStoredAccessToken();
  if (accessToken && fetchTargetIsTrusted(url)) {
    headers.set('X-CLADEX-Access-Token', accessToken);
  }
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    if (response.status === 401 && payload?.authRequired) {
      throw new RemoteAccessTokenError(payload?.error || 'CLADEX remote access token required.');
    }
    throw new ApiRequestError(payload?.error || 'Request failed', response.status);
  }
  return response.json();
}

async function fetchOptionalJson<T>(url: string, fallback: T): Promise<T> {
  try {
    return await fetchJson<T>(url);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) {
      return fallback;
    }
    throw error;
  }
}

const api = {
  profiles: () => fetchJson<Profile[]>(`${API_BASE}/profiles`),
  projects: () => fetchJson<ProjectRecord[]>(`${API_BASE}/projects`),
  runtimeInfo: () => fetchJson<RuntimeInfo>(`${API_BASE}/runtime-info`),
  logs: (id: string, relayType: RelayType) => fetchJson<{ logs: string[] }>(`${API_BASE}/profiles/${id}/logs?type=${relayType}`),
  chatHistory: (id: string, relayType: RelayType) => fetchJson<{ messages: ChatMessageRecord[] }>(`${API_BASE}/profiles/${id}/chat/history?type=${relayType}`),
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
  reviews: () => fetchJson<ReviewJob[]>(`${API_BASE}/reviews`),
  fixRuns: () => fetchOptionalJson<FixRun[]>(`${API_BASE}/fix-runs`, []),
  fixRun: (id: string) => fetchJson<FixRun>(`${API_BASE}/fix-runs/${id}`),
  backups: () => fetchJson<BackupRecord[]>(`${API_BASE}/backups`),
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
  const bootFailureCount = useRef(0);
  const loadAllInFlight = useRef(false);
  const isDark = true;

  useEffect(() => {
    document.documentElement.classList.add('dark');
  }, [isDark]);

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
      const [profileRows, projectRows, runtime, reviews, fixRunRows, backupRows] = await Promise.all([
        api.profiles(),
        api.projects(),
        api.runtimeInfo(),
        api.reviews(),
        api.fixRuns(),
        api.backups(),
      ]);
      setProfiles(profileRows);
      setProjects(projectRows);
      setReviewJobs(reviews);
      const detailedFixRuns = await Promise.all(
        fixRunRows.map((run) => (isInFlightStatus(run.status) ? api.fixRun(run.id).catch(() => run) : Promise.resolve(run)))
      );
      setFixRuns(detailedFixRuns);
      setBackups(backupRows);
      setRuntimeInfo(runtime);
      setRemoteAuthRequired(false);
      bootFailureCount.current = 0;
      setBootPending(false);
      setErrorText('');
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
    if (!selectedProfileId || !profiles.some((profile) => profile.id === selectedProfileId)) {
      setSelectedProfileId(profiles[0].id);
    }
  }, [profiles, selectedProfileId]);

  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId) || null;

  async function runAction(key: string, action: () => Promise<unknown>) {
    setBusyKey(key);
    try {
      await action();
      await loadAll(true);
      setErrorText('');
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : 'Action failed.');
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className={`relative min-h-screen overflow-hidden font-sans transition-colors duration-500 selection:bg-indigo-500/30 ${isDark ? 'bg-[#050505] text-gray-100' : 'bg-[#f2efe7] text-slate-900'}`}>
      <CladexBackground isDark={isDark} />
      <div className={`pointer-events-none absolute inset-0 z-0 transition-opacity duration-500 ${isDark ? 'bg-[radial-gradient(circle_at_top,rgba(249,115,22,0.12),transparent_28%),radial-gradient(circle_at_bottom_right,rgba(16,185,129,0.12),transparent_32%)] opacity-100' : 'bg-[radial-gradient(circle_at_top,rgba(212,115,94,0.16),transparent_30%),radial-gradient(circle_at_bottom_right,rgba(125,181,165,0.18),transparent_34%)] opacity-80'}`} />
      <main className="relative z-10 flex min-h-screen flex-col overflow-y-auto pb-24 sm:pb-28">
        <header className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 pb-2 pt-5 sm:px-8 sm:pt-7 lg:flex-row lg:items-start lg:justify-between lg:gap-6">
          <div className="flex items-center gap-4">
            <div className={`relative h-12 w-12 overflow-hidden rounded-[18px] border shadow-[0_0_28px_rgba(99,102,241,0.16)] ${isDark ? 'border-white/10 bg-white/5' : 'border-black/10 bg-white/70 shadow-[0_0_30px_rgba(212,115,94,0.12)]'}`}>
              <img src={CLADEX_LOGO} alt="CLADEX" className="h-full w-full object-cover" />
            </div>
            <div>
              <h1 className={`text-[1.9rem] leading-none font-black tracking-tight sm:text-[2.15rem] ${isDark ? 'text-white' : 'text-slate-900'}`}>ClaDex</h1>
              <p className={`mt-1.5 font-mono text-[11px] uppercase tracking-[0.32em] ${isDark ? 'text-orange-300/85' : 'text-[#b15f4e]'}`}>Unified Relay Network</p>
            </div>
          </div>
          <div className="flex gap-2 self-start lg:pt-4">
            <MiniIconButton label="Refresh" icon={<RefreshCw size={15} />} onClick={() => void loadAll()} />
            <MiniIconButton label="Stop All" icon={<PauseCircle size={15} />} tone="danger" onClick={() => void runAction('stop-all', api.stopAll)} />
          </div>
        </header>

        {!bootPending && errorText ? <div className={`mx-auto mt-3 w-full max-w-7xl rounded-2xl border px-4 py-3 text-sm ${isDark ? 'border-amber-500/20 bg-amber-500/10 text-amber-100' : 'border-amber-300 bg-amber-50 text-amber-950'}`}>{errorText}</div> : null}

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
                onStart={(profile) => void runAction(`start-${profile.id}`, () => api.startRelay(profile.id, profile.relayType))}
                onStop={(profile) => void runAction(`stop-${profile.id}`, () => api.stopRelay(profile.id, profile.relayType))}
                onRestart={(profile) => void runAction(`restart-${profile.id}`, () => api.restartRelay(profile.id, profile.relayType))}
                onDelete={(profile) => void runAction(`delete-${profile.id}`, () => api.deleteProfile(profile.id, profile.relayType))}
                onEdit={(profile) => {
                  setSelectedProfileId(profile.id);
                  setActiveModal('edit');
                }}
                onLogs={(profile) => {
                  setSelectedProfileId(profile.id);
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
              <LiveFeed profiles={profiles} selectedProfileId={selectedProfileId} onSelectProfile={setSelectedProfileId} />
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      <div className="fixed inset-x-3 bottom-3 z-50 sm:inset-x-auto sm:bottom-6 sm:left-1/2 sm:-translate-x-1/2">
        <div className={`flex items-center justify-between gap-1 overflow-x-auto rounded-2xl border p-2 backdrop-blur-xl shadow-2xl transition-colors duration-500 sm:gap-2 ${isDark ? 'border-white/10 bg-white/5 shadow-black/50' : 'border-slate-300/70 bg-white/80 shadow-slate-300/50'}`}>
          <DockButton icon={<LayoutGrid />} label="Relays" active={view === 'relays'} onClick={() => setView('relays')} light={!isDark} />
          <DockButton icon={<FolderKanban />} label="Workgroups" active={view === 'workgroups'} onClick={() => setView('workgroups')} light={!isDark} />
          <DockButton icon={<SearchCheck />} label="Review Project" active={view === 'review'} onClick={() => setView('review')} light={!isDark} />
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
              storeAccessToken(remoteAccessTokenDraft);
              await loadAll();
            }}
          />
        ) : null}
        {activeModal === 'add' ? <AddProfileModal onClose={() => setActiveModal(null)} onSubmit={async (data) => { await runAction('create-profile', () => api.createProfile(data)); setActiveModal(null); }} /> : null}
        {activeModal === 'edit' && selectedProfile ? <EditProfileModal profile={selectedProfile} onClose={() => setActiveModal(null)} onSubmit={async (data) => { await runAction(`update-${selectedProfile.id}`, () => api.updateProfile(selectedProfile.id, selectedProfile.relayType, data)); setActiveModal(null); }} /> : null}
        {activeModal === 'logs' && selectedProfile ? <LogsModal profile={selectedProfile} onClose={() => setActiveModal(null)} /> : null}
        {activeModal === 'settings' ? <SettingsModal runtimeInfo={runtimeInfo} onClose={() => setActiveModal(null)} onStopAll={() => void runAction('stop-all', api.stopAll)} /> : null}
        {activeModal === 'workgroup' ? <WorkgroupModal profiles={profiles} onClose={() => setActiveModal(null)} onSubmit={async (name, members) => { await runAction(`workgroup-${name}`, () => api.createProject(name, members)); setActiveModal(null); }} /> : null}
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
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 pb-10 pt-4 sm:px-8">
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
            <React.Fragment key={profile.id}>
              <RelayCard
                profile={profile}
                busy={Boolean(busyKey?.includes(profile.id))}
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
  const tiltX = useMotionValue(0);
  const tiltY = useMotionValue(0);
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);
  const springX = useSpring(tiltX, { stiffness: 280, damping: 26, mass: 0.4 });
  const springY = useSpring(tiltY, { stiffness: 280, damping: 26, mass: 0.4 });
  const rotateX = useTransform(springY, [-0.5, 0.5], ['8deg', '-8deg']);
  const rotateY = useTransform(springX, [-0.5, 0.5], ['-8deg', '8deg']);
  const spotlight = useMotionTemplate`radial-gradient(300px circle at ${pointerX}px ${pointerY}px, ${isClaude ? 'rgba(212,115,94,0.18)' : 'rgba(125,181,165,0.18)'}, transparent 48%)`;

  function handlePointerMove(event: React.MouseEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    pointerX.set(event.clientX - bounds.left);
    pointerY.set(event.clientY - bounds.top);
    tiltX.set((event.clientX - (bounds.left + bounds.width / 2)) / bounds.width);
    tiltY.set((event.clientY - (bounds.top + bounds.height / 2)) / bounds.height);
  }

  function resetPointer() {
    tiltX.set(0);
    tiltY.set(0);
  }

  return (
    <motion.div
      onMouseMove={handlePointerMove}
      onMouseLeave={resetPointer}
      style={{ rotateX, rotateY, transformStyle: 'preserve-3d' }}
      whileHover={{ scale: 1.01 }}
      className="group relative h-[262px] sm:h-[276px] [perspective:1200px]"
    >
      <div className="absolute inset-0 rounded-[32px] bg-black/25 blur-2xl dark:bg-black/35" />
      <div className="relative h-full overflow-hidden rounded-[28px] border border-slate-200/70 bg-white/70 p-5 shadow-[0_18px_44px_rgba(15,23,42,0.12)] backdrop-blur-xl transition-colors duration-500 dark:border-white/10 dark:bg-[#09090b]/90 dark:shadow-2xl">
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#0f172a08_1px,transparent_1px),linear-gradient(to_bottom,#0f172a08_1px,transparent_1px)] bg-[size:24px_24px] opacity-60 dark:bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)]" />
        <motion.div className="pointer-events-none absolute inset-0 rounded-[28px] opacity-0 transition-opacity duration-300 group-hover:opacity-100" style={{ background: spotlight }} />
        <div className="pointer-events-none absolute -right-14 top-8 h-24 w-24 rounded-full blur-3xl" style={{ background: `${accent}28` }} />
        <div className="relative z-10 flex h-full flex-col">
        <div className="flex items-start justify-between gap-3 sm:gap-4">
          <div>
            <div className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.24em] ${isClaude ? 'border-orange-500/30 bg-orange-500/10 text-orange-700 dark:text-orange-200' : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200'}`}>{profile.type}</div>
            <h3 className="mt-3 text-[1.55rem] leading-none font-bold tracking-tight text-slate-900 sm:text-[1.9rem] dark:text-white">{labelFor(profile)}</h3>
            <p className="mt-2 text-sm text-slate-500 dark:text-gray-400"># {workspaceFor(profile)}</p>
          </div>
          <div className="flex gap-1.5 sm:gap-2">
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
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 pb-8 pt-6 sm:px-8 sm:pt-8">
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
  onStart: (body: { workspace: string; provider: ReviewProvider; agents: number; title?: string; accountHome?: string; allowSelfReview?: boolean; backupBeforeReview?: boolean }) => void;
  onFixPlan: (job: ReviewJob) => void;
  onFixReview: (job: ReviewJob, options?: { allowSelfFix?: boolean }) => void;
  onCancel: (job: ReviewJob) => void;
  onCancelFixRun: (run: FixRun) => void;
  onCreateBackup: (workspace: string) => void;
}) {
  const [workspace, setWorkspace] = useState('');
  const [title, setTitle] = useState('');
  const [provider, setProvider] = useState<ReviewProvider>('codex');
  const [agents, setAgents] = useState(8);
  const [codexAccountHome, setCodexAccountHome] = useState('');
  const [claudeAccountHome, setClaudeAccountHome] = useState('');
  const [allowSelfReview, setAllowSelfReview] = useState(false);
  const [backupBeforeReview, setBackupBeforeReview] = useState(true);
  const accountHome = provider === 'codex' ? codexAccountHome : claudeAccountHome;
  const setAccountHome = provider === 'codex' ? setCodexAccountHome : setClaudeAccountHome;
  const reviewBusy = busyKey === 'review-start';
  const backupBusy = busyKey === 'backup-create';
  const workspaceFilled = workspace.trim().length > 0;
  const activeJobs = jobs.filter((job) => job.status === 'queued' || job.status === 'running').length;
  const activeFixRuns = fixRuns.filter((run) => run.status === 'queued' || run.status === 'running').length;

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 pb-8 pt-6 sm:px-8 sm:pt-8">
      <div className="mb-6 flex flex-col gap-4 sm:mb-8 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.24em] text-slate-500 dark:text-gray-500">Project review swarm</div>
          <h2 className="mt-2 text-3xl font-black tracking-tight text-slate-900 dark:text-white">Send read-only reviewers through a project.</h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-600 dark:text-gray-400">Each lane gets a different focus, explores a separate shard, and merges findings into one report plus a fix plan. Reviewers do not apply source changes.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <MetaPill label={`${activeJobs} active review${activeJobs === 1 ? '' : 's'}`} />
          <MetaPill label={`${activeFixRuns} active fix run${activeFixRuns === 1 ? '' : 's'}`} />
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
        <div className="rounded-[30px] border border-slate-200/80 bg-white/80 p-5 shadow-[0_18px_45px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-2xl">
          <div className="space-y-5">
            <FormSection title="Target">
              <BrowseField label="Project folder" value={workspace} onChange={setWorkspace} placeholder="C:\\Projects\\target-repo" />
              <FormInput label="Review title" value={title} onChange={setTitle} placeholder="Production readiness pass" />
            </FormSection>

            <FormSection title="Review lanes">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <TypeButton active={provider === 'codex'} label="Codex" icon={<Terminal size={18} />} onClick={() => setProvider('codex')} tone="emerald" />
                <TypeButton active={provider === 'claude'} label="Claude Code" icon={<Bot size={18} />} onClick={() => setProvider('claude')} tone="orange" />
              </div>
              <label className="block">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Reviewer count</div>
                  <div className="font-mono text-sm text-slate-700 dark:text-gray-300">{agents}</div>
                </div>
                <input
                  type="range"
                  min={1}
                  max={50}
                  value={agents}
                  onChange={(event) => setAgents(Number(event.target.value))}
                  className="w-full accent-indigo-500"
                />
              </label>
              <BrowseField
                label={provider === 'codex' ? 'Codex account home' : 'Claude config folder'}
                value={accountHome}
                onChange={setAccountHome}
                placeholder="Optional account home for this review run"
              />
              <ToggleRow checked={backupBeforeReview} onChange={setBackupBeforeReview} label="Save a source snapshot before review" />
              <ToggleRow checked={allowSelfReview} onChange={setAllowSelfReview} label="Allow explicit CLADEX self-review" />
            </FormSection>

            <PrimaryButton
              label={reviewBusy ? 'Starting...' : 'Review Project'}
              icon={reviewBusy ? <Loader2 size={16} className="animate-spin" /> : <SearchCheck size={16} />}
              busy={reviewBusy || backupBusy || !workspaceFilled}
              onClick={() => {
                if (!workspaceFilled || reviewBusy || backupBusy) return;
                onStart({ workspace, provider, agents, title, accountHome, allowSelfReview, backupBeforeReview });
              }}
            />
            <SecondaryButton
              label={backupBusy ? 'Saving snapshot...' : 'Save snapshot only'}
              busy={backupBusy || reviewBusy || !workspaceFilled}
              onClick={() => {
                if (!workspaceFilled || reviewBusy || backupBusy) return;
                onCreateBackup(workspace);
              }}
            />
          </div>
        </div>

        <div className="space-y-4">
          {jobs.length === 0 ? (
            <EmptyState title="No project reviews yet." detail="Pick a project folder, choose Codex or Claude lanes, then start a read-only review." />
          ) : (
            jobs.map((job) => (
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
            ))
          )}
          <FixRunsPanel
            runs={fixRuns}
            busyKey={busyKey}
            onCancel={onCancelFixRun}
          />
          <BackupListCard backups={backups} />
        </div>
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

function maxParallelFor(record: LimitAwareRecord): number | null {
  return firstNumber(record.maxParallel, record.maxWorkers, record.maxAgents, record.progress?.maxParallel, record.progress?.maxWorkers, record.limits?.maxParallel, record.limits?.maxWorkers);
}

function progressFor(progress: ReviewProgress | undefined, fallbackTotal: number): ReviewProgress {
  return progress || { total: fallbackTotal, queued: 0, running: 0, done: 0, failed: 0, cancelled: 0 };
}

function ProgressCounts({ progress, total }: { progress: ReviewProgress; total: number }) {
  return (
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm text-slate-600 dark:text-gray-400">
      <span>Queued {progress.queued || 0}/{total}</span>
      <span>Running {progress.running || 0}/{total}</span>
      <span>Done {progress.done || 0}/{total}</span>
      <span>Failed {progress.failed || 0}/{total}</span>
      <span>Cancelled {progress.cancelled || 0}/{total}</span>
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
    <div className="mt-4 rounded-2xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
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
            <div key={task.id} className="rounded-2xl border border-slate-200/80 bg-white/70 px-3 py-3 dark:border-white/5 dark:bg-black/30">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 truncate font-mono text-xs text-slate-700 dark:text-gray-300">{task.title || task.id}</div>
                <div className="shrink-0 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-gray-500">{statusLabel(task.status)}</div>
              </div>
              {task.detail ? <div className="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-gray-500">{task.detail}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function BackupListCard({ backups }: { backups: BackupRecord[] }) {
  const recent = backups.slice(0, 8);
  return (
    <div className="rounded-[30px] border border-slate-200/80 bg-white/80 p-5 shadow-[0_18px_45px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-2xl">
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
  const canFixReview = job.status === 'completed' || job.status === 'completed_with_warnings';
  const severity = job.severityCounts || { high: 0, medium: 0, low: 0 };
  const totalFindings = severity.high + severity.medium + severity.low;

  return (
    <div className="rounded-[30px] border border-slate-200/80 bg-white/80 p-5 shadow-[0_18px_45px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-2xl">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.22em] ${statusTone(job.status)}`}>{statusLabel(job.status)}</span>
            <MetaPill label={`${job.provider} swarm`} mono />
            <MetaPill label={`${job.agentCount} lane${job.agentCount === 1 ? '' : 's'}`} mono />
            {job.selfReview ? <MetaPill label="CLADEX self-review" /> : null}
            {job.cancelRequested && inFlight ? <MetaPill label="cancel pending" /> : null}
          </div>
          <h3 className="mt-3 text-xl font-bold tracking-tight text-slate-900 dark:text-white">{job.title || job.id}</h3>
          <div className="mt-2 break-all font-mono text-xs text-slate-500 dark:text-gray-500">{job.workspace}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <ActionButton
            label={fixPlanBusy ? 'Planning...' : 'Fix Plan'}
            icon={fixPlanBusy ? <Loader2 size={16} className="animate-spin" /> : <FileText size={16} />}
            busy={fixPlanBusy || inFlight}
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
      </div>

      {totalFindings > 0 ? (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px]">
          <span className="rounded-full border border-red-500/25 bg-red-500/10 px-2.5 py-1 font-semibold text-red-200">High {severity.high}</span>
          <span className="rounded-full border border-amber-500/25 bg-amber-500/10 px-2.5 py-1 font-semibold text-amber-200">Medium {severity.medium}</span>
          <span className="rounded-full border border-slate-500/25 bg-slate-500/10 px-2.5 py-1 font-semibold text-slate-200">Low {severity.low}</span>
        </div>
      ) : null}

      <div className="mt-5">
        <ProgressCounts progress={progress} total={total} />
        <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10">
          <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${percent}%` }} />
        </div>
      </div>

      <LimitNotice record={job} requested={job.agentCount || total} />

      <div className="mt-5 grid gap-3 lg:grid-cols-2">
        <InspectorRow label="Report" value={job.reportPath || 'Pending'} mono />
        <InspectorRow label="Fix plan" value={job.fixPlanPath || 'Not generated'} mono />
        <InspectorRow label="Backup" value={job.sourceBackup?.id || job.sourceBackup?.error || 'Not created'} mono />
      </div>

      {job.error ? <div className="mt-4 rounded-2xl border border-red-500/25 bg-red-500/10 px-4 py-3 text-sm text-red-100">{job.error}</div> : null}

      {job.agents?.length ? (
        <div className="mt-5 grid gap-2 md:grid-cols-2">
          {job.agents.slice(0, 8).map((agent) => (
            <div key={agent.id} className="rounded-2xl border border-slate-200/80 bg-white/70 px-3 py-3 dark:border-white/5 dark:bg-black/30">
              <div className="flex items-center justify-between gap-3">
                <div className="font-mono text-xs text-slate-700 dark:text-gray-300">{agent.id}</div>
                <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500 dark:text-gray-500">{agent.status}</div>
              </div>
              <div className="mt-1 text-xs text-slate-500 dark:text-gray-500">{agent.focus || 'review'} - {agent.assignedFiles} files, {agent.findings} findings</div>
            </div>
          ))}
        </div>
      ) : null}

      {job.reportPreview ? (
        <details className="mt-5">
          <summary className="cursor-pointer text-sm font-semibold text-indigo-300">Report preview</summary>
          <pre className="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap rounded-2xl border border-white/5 bg-black p-4 text-xs leading-relaxed text-gray-300">{job.reportPreview}</pre>
        </details>
      ) : null}

      {(job.status === 'completed' || job.status === 'completed_with_warnings' || job.status === 'failed') ? (
        <FindingsExplorer jobId={job.id} totalFindings={totalFindings} />
      ) : null}
    </div>
  );
}

function FindingsExplorer({ jobId, totalFindings }: { jobId: string; totalFindings: number }) {
  const [open, setOpen] = useState(false);
  const [findings, setFindings] = useState<ReviewFinding[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [severityFilter, setSeverityFilter] = useState<Record<'high' | 'medium' | 'low', boolean>>({
    high: true,
    medium: true,
    low: true,
  });
  const [categoryFilter, setCategoryFilter] = useState<string>('');

  useEffect(() => {
    if (!open || findings !== null || loading) return;
    let cancelled = false;
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
  }, [open, findings, loading, jobId]);

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
        Findings explorer{totalFindings > 0 ? ` (${totalFindings})` : ''}
      </summary>
      <div className="mt-3 rounded-2xl border border-white/5 bg-black/40 p-4">
        {loading ? <div className="text-xs text-gray-400">Loading findings...</div> : null}
        {errorText ? <div className="text-xs text-red-300">{errorText}</div> : null}
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

function LiveFeed({
  profiles,
  selectedProfileId,
  onSelectProfile,
}: {
  profiles: Profile[];
  selectedProfileId: string | null;
  onSelectProfile: (value: string) => void;
}) {
  const workspaces = Array.from(new Set(profiles.map((profile) => profile.workspace))).sort();
  const [activeWorkspace, setActiveWorkspace] = useState(workspaces[0] || '');
  const [messages, setMessages] = useState<ChatMessageRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [draft, setDraft] = useState('');
  const workspaceProfiles = profiles.filter((profile) => profile.workspace === activeWorkspace);
  const activeProfile = workspaceProfiles.find((profile) => profile.id === selectedProfileId) || workspaceProfiles[0] || null;

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
    if (activeProfile && activeProfile.id !== selectedProfileId) {
      onSelectProfile(activeProfile.id);
    }
  }, [activeProfile, onSelectProfile, selectedProfileId]);

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
          setMessages(payload.messages || []);
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

  async function sendMessage() {
    if (!activeProfile || !draft.trim() || sending) {
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
          senderName: labelFor(activeProfile),
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
          senderName: labelFor(activeProfile),
          timestamp: new Date().toISOString(),
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 pb-8 pt-6 sm:px-8 sm:pt-8">
      <div className="overflow-hidden rounded-[32px] border border-slate-200/80 bg-[#fbfaf6] shadow-[0_22px_60px_rgba(15,23,42,0.12)] dark:border-white/10 dark:bg-[#0a0a0c] dark:shadow-2xl">
        <div className="flex gap-2 overflow-x-auto border-b border-slate-200 bg-white/60 px-4 py-4 sm:flex-wrap sm:px-5 dark:border-white/5 dark:bg-white/[0.03]">
          {workspaces.map((workspace) => (
            <button key={workspace} onClick={() => setActiveWorkspace(workspace)} className={`rounded-2xl px-4 py-2 text-sm font-medium transition-colors ${activeWorkspace === workspace ? 'border border-indigo-500/30 bg-indigo-500/15 text-indigo-700 dark:text-indigo-200' : 'text-slate-500 hover:bg-black/5 hover:text-slate-900 dark:text-gray-500 dark:hover:bg-white/5 dark:hover:text-gray-200'}`}>
              {workspace.split(/[\\/]/).filter(Boolean).pop() || workspace}
            </button>
          ))}
        </div>
        <div className="grid min-h-[640px] grid-cols-1 xl:grid-cols-[260px_minmax(0,1fr)_320px]">
          <div className="border-b border-slate-200 bg-white/30 p-4 xl:border-b-0 xl:border-r xl:p-5 dark:border-white/5 dark:bg-black/20">
            <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Relays in this workspace</div>
            <div className="mt-4 space-y-3">
              {workspaceProfiles.map((profile) => (
                <button key={profile.id} onClick={() => onSelectProfile(profile.id)} className={`w-full rounded-2xl border px-4 py-3 text-left transition-colors ${activeProfile?.id === profile.id ? 'border-indigo-500/30 bg-indigo-500/10' : 'border-slate-200 bg-white/70 hover:bg-slate-50 dark:border-white/5 dark:bg-white/[0.02] dark:hover:bg-white/[0.06]'}`}>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-slate-900 dark:text-white">{labelFor(profile)}</div>
                      <div className="text-xs text-slate-500 dark:text-gray-500">{profile.type}</div>
                    </div>
                    <span className={`h-2.5 w-2.5 rounded-full ${profile.running ? (profile.type === 'Claude' ? 'bg-orange-400' : 'bg-emerald-400') : 'bg-slate-400 dark:bg-gray-600'}`} />
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="border-b border-slate-200 xl:border-b-0 xl:border-r dark:border-white/5">
            <div className="border-b border-slate-200 bg-white/60 px-4 py-5 sm:px-6 dark:border-white/5 dark:bg-white/[0.03]">
              {activeProfile ? (
                <>
                  <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Local operator chat</div>
                  <div className="mt-2 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{labelFor(activeProfile)}</div>
                  <div className="mt-2 text-sm text-slate-600 dark:text-gray-400">
                    Chat with the same running relay session from inside CLADEX. Discord is still live; this is just the local operator surface.
                  </div>
                </>
              ) : (
                <div className="text-slate-500 dark:text-gray-500">Select a relay to inspect it.</div>
              )}
            </div>
            <div className="flex h-[520px] sm:h-[560px] flex-col bg-[#f7f3ea]/70 dark:bg-black/30">
              <div className="flex-1 overflow-y-auto p-4 sm:p-6">
              {!activeProfile ? (
                <EmptyState title="No relay selected." detail="Pick a relay on the left to inspect its feed." compact />
              ) : loading && !messages.length ? (
                <div className="flex items-center gap-2 text-indigo-500 dark:text-indigo-300"><Loader2 size={16} className="animate-spin" /> Loading local chat history...</div>
              ) : messages.length ? (
                <div className="space-y-4">
                  {messages.map((message) => {
                    const assistant = message.role === 'assistant';
                    return (
                      <div key={message.id} className={`flex ${assistant ? 'justify-start' : 'justify-end'}`}>
                        <div className={`max-w-[85%] rounded-[22px] border px-4 py-3 text-sm leading-relaxed shadow-sm ${assistant ? 'border-slate-200 bg-white/85 text-slate-800 dark:border-white/5 dark:bg-white/[0.04] dark:text-gray-200' : 'border-indigo-500/25 bg-indigo-500/12 text-indigo-900 dark:text-indigo-100'}`}>
                          <div className={`mb-1 text-[10px] font-bold uppercase tracking-[0.22em] ${assistant ? 'text-slate-400 dark:text-gray-500' : 'text-indigo-400'}`}>
                            {assistant ? (message.senderName || labelFor(activeProfile)) : (message.senderName || 'Operator')}
                          </div>
                          <div className="whitespace-pre-wrap break-words">{message.content}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <EmptyState title="No local chat yet." detail="Send a message here to talk to the running relay without using Discord." compact />
              )}
              </div>
              <div className="border-t border-slate-200 bg-white/70 p-4 dark:border-white/5 dark:bg-white/[0.03]">
                <div className="flex flex-col gap-3 sm:flex-row">
                  <textarea
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault();
                        void sendMessage();
                      }
                    }}
                    placeholder={activeProfile ? `Message ${labelFor(activeProfile)} here instead of Discord...` : 'Select a relay first'}
                    disabled={!activeProfile || sending}
                    className="min-h-[88px] flex-1 resize-none rounded-[22px] border border-slate-200 bg-white/85 px-4 py-3 text-sm text-slate-900 outline-none transition-colors focus:border-indigo-500 dark:border-white/10 dark:bg-black/40 dark:text-white"
                  />
                  <button
                    onClick={() => void sendMessage()}
                    disabled={!activeProfile || !draft.trim() || sending}
                    className="inline-flex min-w-[120px] items-center justify-center gap-2 self-stretch rounded-[22px] bg-indigo-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 sm:self-end"
                  >
                    {sending ? <Loader2 size={16} className="animate-spin" /> : <MessageSquare size={16} />}
                    Send
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-white/40 p-4 sm:p-6 dark:bg-white/[0.03]">
            <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Relay details</div>
            {activeProfile ? (
              <div className="mt-4 space-y-4">
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
              <div className="mt-4 text-sm text-slate-500 dark:text-gray-500">No relay selected.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function AddProfileModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: (data: ProfileFormData) => Promise<void> }) {
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
  const [saving, setSaving] = useState(false);

  const codex = type === 'Codex';
  const accessError = profileCreateAccessError(type, channelId, allowDms, operatorIds, allowedUserIds);
  const canSave = Boolean(name.trim() && workspace.trim() && discordToken.trim() && !accessError);

  return (
    <ModalShell title="Add Relay" onClose={onClose} wide>
      <div className="space-y-6">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4">
          <TypeButton active={type === 'Claude'} label="Claude Code" icon={<Bot size={18} />} onClick={() => setType('Claude')} tone="orange" />
          <TypeButton active={type === 'Codex'} label="Codex" icon={<Terminal size={18} />} onClick={() => setType('Codex')} tone="emerald" />
        </div>

        <FormSection title="Basics">
          <FormInput label="Bot label" value={name} onChange={setName} placeholder="Tyson" />
          <BrowseField label="Workspace folder" value={workspace} onChange={setWorkspace} placeholder="C:\\Projects\\my-repo" />
          <FormInput label="Discord bot token" value={discordToken} onChange={setDiscordToken} placeholder="Paste token" type="password" />
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Allowed channel IDs" value={channelId} onChange={setChannelId} placeholder="123456789012345678, 234567890123456789" mono />
            <FormInput label="Model override" value={model} onChange={setModel} placeholder={codex ? 'Codex default' : 'Claude default'} mono />
          </div>
          {codex ? (
            <BrowseField label="Codex account home" value={codexHome} onChange={setCodexHome} placeholder="Optional CODEX_HOME for this relay account" />
          ) : (
            <BrowseField label="Claude config folder" value={claudeConfigDir} onChange={setClaudeConfigDir} placeholder="Optional CLAUDE_CONFIG_DIR for this relay account" />
          )}
        </FormSection>

        <FormSection title="Access">
          <div className="grid gap-4 md:grid-cols-2">
            <FormSelect label="Trigger mode" value={triggerMode} onChange={setTriggerMode} options={[{ value: 'mention_or_dm', label: 'Mention or direct message' }, { value: 'all', label: 'Every message in the channel' }, { value: 'dm_only', label: 'Direct messages only' }]} />
            <FormInput label={codex ? 'Approved DM user IDs' : 'Approved user IDs'} value={allowedUserIds} onChange={setAllowedUserIds} placeholder="Comma-separated Discord user IDs" mono />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Operator IDs" value={operatorIds} onChange={setOperatorIds} placeholder="Comma-separated Discord user IDs" mono />
            <FormInput label="Allowed bot IDs" value={allowedBotIds} onChange={setAllowedBotIds} placeholder="Comma-separated Discord bot IDs for bot-to-bot chat" mono />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Channel history limit" value={channelHistoryLimit} onChange={setChannelHistoryLimit} placeholder="20" mono />
          </div>
          {codex ? (
            <div className="grid gap-4 md:grid-cols-2">
              <FormInput label="Allowed channel author IDs" value={allowedChannelAuthorIds} onChange={setAllowedChannelAuthorIds} placeholder="Comma-separated Discord user IDs" mono />
              <FormInput label="No-mention author IDs" value={channelNoMentionAuthorIds} onChange={setChannelNoMentionAuthorIds} placeholder="Comma-separated Discord user IDs" mono />
            </div>
          ) : null}
          <ToggleRow checked={allowDms} onChange={setAllowDms} label="Allow direct messages for approved users" />
        </FormSection>

        {codex ? (
          <FormSection title="Startup">
            <FormInput label="Startup DM user IDs" value={startupDmUserIds} onChange={setStartupDmUserIds} placeholder="Comma-separated Discord user IDs" mono />
            <FormInput label="Startup DM text" value={startupDmText} onChange={setStartupDmText} placeholder="Discord relay online. DM me here to chat with Codex." />
            <FormInput label="Startup channel text" value={startupChannelText} onChange={setStartupChannelText} placeholder="Optional message posted in the main channel on startup" />
          </FormSection>
        ) : null}

        {accessError ? (
          <div className="rounded-2xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm font-medium text-amber-800 dark:text-amber-100">
            {accessError}
          </div>
        ) : null}

        <div className="flex flex-col-reverse justify-end gap-3 pt-2 sm:flex-row">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Save relay'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} onClick={async () => {
            if (!canSave) return;
            setSaving(true);
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
            } finally {
              setSaving(false);
            }
          }} busy={saving} disabled={!canSave} />
        </div>
      </div>
    </ModalShell>
  );
}

function EditProfileModal({ profile, onClose, onSubmit }: { profile: Profile; onClose: () => void; onSubmit: (data: ProfileSettingsData) => Promise<void> }) {
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
  const [saving, setSaving] = useState(false);

  const codex = profile.type === 'Codex';

  return (
    <ModalShell title={`Edit ${labelFor(profile)}`} onClose={onClose} wide>
      <div className="space-y-6">
        <FormSection title="Basics">
          <InspectorRow label="Relay type" value={profile.type} />
          <BrowseField label="Workspace folder" value={workspace} onChange={setWorkspace} placeholder="C:\\Projects\\my-repo" />
          <FormInput label="Bot label" value={botName} onChange={setBotName} placeholder="Tyson" />
          <FormInput label="Replace bot token" value={discordToken} onChange={setDiscordToken} placeholder="Leave blank to keep the current token" type="password" />
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Allowed channel IDs" value={channelId} onChange={setChannelId} placeholder="123456789012345678, 234567890123456789" mono />
            <FormInput label="Model" value={model} onChange={setModel} placeholder={codex ? 'Codex default' : 'Claude default'} mono />
          </div>
          {codex ? (
            <BrowseField label="Codex account home" value={codexHome} onChange={setCodexHome} placeholder="Optional CODEX_HOME for this relay account" />
          ) : (
            <BrowseField label="Claude config folder" value={claudeConfigDir} onChange={setClaudeConfigDir} placeholder="Optional CLAUDE_CONFIG_DIR for this relay account" />
          )}
        </FormSection>

        <FormSection title="Access">
          <div className="grid gap-4 md:grid-cols-2">
            <FormSelect label="Trigger mode" value={triggerMode} onChange={setTriggerMode} options={[{ value: 'mention_or_dm', label: 'Mention or direct message' }, { value: 'all', label: 'Every message in the channel' }, { value: 'dm_only', label: 'Direct messages only' }]} />
            <FormInput label={codex ? 'Approved DM user IDs' : 'Approved user IDs'} value={allowedUserIds} onChange={setAllowedUserIds} placeholder="Comma-separated Discord user IDs" mono />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Operator IDs" value={operatorIds} onChange={setOperatorIds} placeholder="Comma-separated Discord user IDs" mono />
            <FormInput label="Allowed bot IDs" value={allowedBotIds} onChange={setAllowedBotIds} placeholder="Comma-separated Discord bot IDs for bot-to-bot chat" mono />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Channel history limit" value={channelHistoryLimit} onChange={setChannelHistoryLimit} placeholder="20" mono />
          </div>
          {codex ? (
            <div className="grid gap-4 md:grid-cols-2">
              <FormInput label="Allowed channel author IDs" value={allowedChannelAuthorIds} onChange={setAllowedChannelAuthorIds} placeholder="Comma-separated Discord user IDs" mono />
              <FormInput label="No-mention author IDs" value={channelNoMentionAuthorIds} onChange={setChannelNoMentionAuthorIds} placeholder="Comma-separated Discord user IDs" mono />
            </div>
          ) : null}
          <ToggleRow checked={allowDms} onChange={setAllowDms} label="Allow direct messages for approved users" />
        </FormSection>

        {codex ? (
          <FormSection title="Startup">
            <FormInput label="Startup DM user IDs" value={startupDmUserIds} onChange={setStartupDmUserIds} placeholder="Comma-separated Discord user IDs" mono />
            <FormInput label="Startup DM text" value={startupDmText} onChange={setStartupDmText} placeholder="Discord relay online. DM me here to chat with Codex." />
            <FormInput label="Startup channel text" value={startupChannelText} onChange={setStartupChannelText} placeholder="Optional message posted in the main channel on startup" />
          </FormSection>
        ) : null}

        <div className="flex flex-col-reverse justify-end gap-3 pt-2 sm:flex-row">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Save changes'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <Pencil size={16} />} onClick={async () => {
            setSaving(true);
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

  return (
    <ModalShell title="Create Workgroup" onClose={onClose} wide>
      <div className="space-y-4">
        <FormInput label="Workgroup name" value={name} onChange={setName} placeholder="Core team" />
        <div className="rounded-2xl border border-white/10 bg-black/30 p-4">
          <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Included relays</div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {profiles.map((profile) => (
              <label key={profile.id} className="flex items-start gap-3 rounded-2xl border border-white/5 bg-white/[0.03] px-4 py-3">
                <input type="checkbox" checked={Boolean(selectedIds[profile.id])} onChange={(event) => setSelectedIds((current) => ({ ...current, [profile.id]: event.target.checked }))} className="mt-1 h-4 w-4 accent-indigo-500" />
                <div>
                  <div className="text-sm font-semibold text-white">{labelFor(profile)}</div>
                  <div className="text-xs text-gray-500">{workspaceFor(profile)} · {profile.type}</div>
                </div>
              </label>
            ))}
          </div>
        </div>
        <div className="flex flex-col-reverse justify-end gap-3 pt-2 sm:flex-row">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Save workgroup'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <FolderKanban size={16} />} onClick={async () => {
            const members = profiles.filter((profile) => selectedIds[profile.id]).map((profile) => ({ id: profile.id, relayType: profile.relayType }));
            if (!name || !members.length) return;
            setSaving(true);
            try {
              await onSubmit(name, members);
            } finally {
              setSaving(false);
            }
          }} />
        </div>
      </div>
    </ModalShell>
  );
}

function SettingsModal({ runtimeInfo, onClose, onStopAll }: { runtimeInfo: RuntimeInfo | null; onClose: () => void; onStopAll: () => void }) {
  return (
    <ModalShell title="CLADEX Runtime" onClose={onClose} wide>
      <div className="space-y-6">
        <p className="text-sm leading-relaxed text-slate-600 dark:text-gray-400">This panel shows the real runtime state. Profile behavior lives with each relay, not in fake global settings.</p>
        <InspectorRow label="API base" value={runtimeInfo?.apiBase || 'Loading...'} mono />
        <InspectorRow label="Backend path" value={runtimeInfo?.backendDir || 'Loading...'} mono />
        {runtimeInfo?.frontendDir ? <InspectorRow label="Frontend path" value={runtimeInfo.frontendDir} mono /> : null}
        <InspectorRow label="App version" value={runtimeInfo?.appVersion || 'Loading...'} />
        <InspectorRow label="Packaging" value={runtimeInfo?.packaged ? 'Packaged desktop build' : 'Source build'} />
        {runtimeInfo?.remoteAccessToken ? <InspectorRow label="Remote token" value={runtimeInfo.remoteAccessToken} mono /> : null}
        <div className="rounded-2xl border border-slate-200/80 bg-white/70 p-4 text-sm text-slate-600 dark:border-white/10 dark:bg-black/30 dark:text-gray-400">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Runtime notes</div>
          <ul className="space-y-2">
            <li>Codex stays the deeper runtime because it is app-server based.</li>
            <li>Claude now shares the same durable memory, worktree, status, and handoff path instead of a thin side path.</li>
            <li>Bot labels, trigger mode, model choice, and DM access are managed per relay profile.</li>
          </ul>
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

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const payload = await api.logs(profile.id, profile.relayType);
        if (!cancelled) {
          setLogs(payload.logs || []);
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

  return (
    <ModalShell title={`Live logs · ${labelFor(profile)}`} onClose={onClose} wide>
      <div className="h-80 overflow-y-auto rounded-2xl border border-white/5 bg-black p-4 font-mono text-xs text-gray-300">
        {loading ? <div className="flex items-center gap-2 text-indigo-300"><Loader2 size={14} className="animate-spin" /> Loading logs...</div> : logs.length ? logs.map((line, index) => <div key={`${profile.id}-${index}`}>{line}</div>) : <div className="text-gray-500">No log lines recorded yet for this relay.</div>}
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

function FormInput({ label, value, onChange, placeholder, mono = false, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; placeholder: string; mono?: boolean; type?: string }) {
  return <label className="block"><div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">{label}</div><input type={type} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className={`w-full rounded-2xl border border-slate-200 bg-white/80 px-4 py-3 text-slate-900 outline-none focus:border-indigo-500 dark:border-white/10 dark:bg-black/40 dark:text-white ${mono ? 'font-mono text-sm' : 'text-sm'}`} /></label>;
}

function BrowseField({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder: string }) {
  const [browserOpen, setBrowserOpen] = useState(false);
  const desktopPickerAvailable = Boolean(window.cladexDesktop?.chooseDirectory);
  return (
    <>
    <label className="block">
      <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">{label}</div>
      <div className="flex flex-col gap-3 sm:flex-row">
        <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="min-w-0 flex-1 rounded-2xl border border-slate-200 bg-white/80 px-4 py-3 text-sm text-slate-900 outline-none focus:border-indigo-500 dark:border-white/10 dark:bg-black/40 dark:text-white" />
        <button
          type="button"
          onClick={async () => {
            if (desktopPickerAvailable) {
              onChange(await chooseWorkspaceFolder(value));
              return;
            }
            setBrowserOpen(true);
          }}
          className="rounded-2xl border border-slate-200 bg-white/80 px-4 py-3 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-100 dark:border-white/10 dark:bg-white/[0.03] dark:text-gray-200 dark:hover:bg-white/[0.08]"
        >
          {desktopPickerAvailable ? 'Browse' : 'Browse server'}
        </button>
      </div>
    </label>
    {browserOpen ? <DirectoryBrowserModal initialPath={value} onClose={() => setBrowserOpen(false)} onPick={(nextPath) => { onChange(nextPath); setBrowserOpen(false); }} /> : null}
    </>
  );
}

function FormSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">{title}</div>
      <div className="space-y-4 rounded-[26px] border border-slate-200/80 bg-white/60 p-4 dark:border-white/10 dark:bg-black/20">
        {children}
      </div>
    </section>
  );
}

function FormSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return <label className="block"><div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">{label}</div><select value={value} onChange={(event) => onChange(event.target.value)} className="w-full rounded-2xl border border-slate-200 bg-white/80 px-4 py-3 text-sm text-slate-900 outline-none focus:border-indigo-500 dark:border-white/10 dark:bg-black/40 dark:text-white">{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
}

function ToggleRow({ checked, onChange, label }: { checked: boolean; onChange: (checked: boolean) => void; label: string }) {
  return <label className="flex items-center gap-3 rounded-2xl border border-slate-200/80 bg-white/80 px-4 py-3 text-sm text-slate-700 dark:border-white/10 dark:bg-black/30 dark:text-gray-300"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4 accent-indigo-500" />{label}</label>;
}

function TypeButton({ active, label, icon, onClick, tone }: { active: boolean; label: string; icon: React.ReactNode; onClick: () => void; tone: 'orange' | 'emerald' }) {
  const activeStyles = tone === 'orange' ? 'border-orange-500/40 bg-orange-500/10 text-orange-200' : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200';
  return <button onClick={onClick} className={`flex items-center justify-center gap-3 rounded-2xl border px-4 py-4 font-semibold transition-colors ${active ? activeStyles : 'border-white/10 bg-white/[0.03] text-gray-400 hover:bg-white/[0.06]'}`}>{icon}{label}</button>;
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
  return (
    <ModalShell title="Remote Access Token" onClose={() => undefined}>
      <div className="space-y-4">
        <p className="text-sm leading-relaxed text-slate-600 dark:text-gray-400">
          This CLADEX instance is being opened from a non-local origin. Enter the CLADEX remote access token from the local Runtime panel to continue.
        </p>
        <FormInput label="Access token" value={token} onChange={onChangeToken} placeholder="Paste the CLADEX remote access token" mono type="password" />
        <div className="flex flex-col-reverse justify-end gap-3 sm:flex-row">
          <SecondaryButton label="Clear saved token" onClick={() => { storeAccessToken(''); onChangeToken(''); }} />
          <PrimaryButton label={submitting ? 'Connecting...' : 'Unlock CLADEX'} icon={submitting ? <Loader2 size={16} className="animate-spin" /> : <Settings size={16} />} onClick={async () => {
            setSubmitting(true);
            try {
              await onSubmit();
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
  return <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-[100] flex items-end justify-center overflow-y-auto bg-black/60 p-3 pt-8 backdrop-blur-sm sm:items-start sm:p-6 sm:pt-12" onClick={onClose}><motion.div initial={{ scale: 0.94, y: 18 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.94, y: 18 }} onClick={(event) => event.stopPropagation()} className={`mb-3 max-h-[calc(100vh-1.5rem)] w-full overflow-hidden rounded-2xl border border-slate-200/80 bg-[#f8f6f0] shadow-[0_28px_80px_rgba(15,23,42,0.22)] sm:mb-12 dark:border-white/10 dark:bg-[#0a0a0c] dark:shadow-2xl ${wide ? 'max-w-2xl' : 'max-w-md'}`}><div className="flex items-center justify-between border-b border-slate-200 bg-white/50 px-4 py-4 sm:px-5 dark:border-white/5 dark:bg-white/[0.03]"><div><div className="text-[9px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-gray-500">CLADEX</div><div className="mt-0.5 text-lg font-semibold text-slate-900 dark:text-white">{title}</div></div><button onClick={onClose} className="rounded-full bg-slate-200/70 p-1.5 text-slate-500 transition-colors hover:bg-slate-300 hover:text-slate-900 dark:bg-white/5 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white"><X size={14} /></button></div><div className="max-h-[calc(100vh-6.5rem)] overflow-y-auto p-4 sm:p-5">{children}</div></motion.div></motion.div>;
}

function DockButton({ icon, label, active, onClick, light = false }: { icon: React.ReactNode; label: string; active?: boolean; onClick: () => void; light?: boolean }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  return <div className="group relative"><motion.button ref={ref} onMouseMove={(event) => { if (!ref.current) return; const bounds = ref.current.getBoundingClientRect(); setPosition({ x: (event.clientX - (bounds.left + bounds.width / 2)) * 0.3, y: (event.clientY - (bounds.top + bounds.height / 2)) * 0.3 }); }} onMouseLeave={() => setPosition({ x: 0, y: 0 })} animate={{ x: position.x, y: position.y }} transition={{ type: 'spring', stiffness: 150, damping: 15, mass: 0.1 }} whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.96 }} onClick={onClick} className={`rounded-xl p-3 transition-colors ${active ? 'bg-indigo-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.5)]' : light ? 'text-slate-600 hover:bg-black/5 hover:text-slate-900' : 'text-gray-400 hover:bg-white/10 hover:text-white'}`}>{icon}</motion.button><div className={`pointer-events-none absolute bottom-full left-1/2 mb-3 -translate-x-1/2 whitespace-nowrap rounded-lg border px-3 py-1.5 text-xs font-bold opacity-0 transition-opacity group-hover:opacity-100 ${light ? 'border-slate-200 bg-white text-slate-800 shadow-xl' : 'border-white/10 bg-black/80 text-white'}`}>{label}</div></div>;
}

function ActionButton({ label, icon, onClick, busy = false, tone = 'default', light = false }: { label: string; icon: React.ReactNode; onClick: () => void; busy?: boolean; tone?: 'default' | 'danger'; light?: boolean }) {
  return <button onClick={onClick} disabled={busy} className={`inline-flex items-center gap-2 rounded-2xl border px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${tone === 'danger' ? 'border-red-500/25 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-200' : light ? 'border-slate-300 bg-white text-slate-800 hover:bg-slate-100' : 'border-white/10 bg-white/[0.04] text-white hover:bg-white/[0.08]'}`}>{busy ? <Loader2 size={16} className="animate-spin" /> : icon}{label}</button>;
}

function PrimaryButton({ label, icon, onClick, busy = false, disabled = false }: { label: string; icon: React.ReactNode; onClick: () => void; busy?: boolean; disabled?: boolean }) {
  return (
    <button
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
  return <button title={label} onClick={onClick} className={`inline-flex h-9 w-9 items-center justify-center rounded-full border transition-colors ${tone === 'danger' ? 'border-red-500/20 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-200' : 'border-slate-200/80 bg-white/70 text-slate-500 hover:bg-slate-200 hover:text-slate-900 dark:border-white/5 dark:bg-white/5 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white'}`}>{icon}</button>;
}
