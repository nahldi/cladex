import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useMotionTemplate, useMotionValue, useSpring, useTransform } from 'motion/react';
import {
  Activity,
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
  Settings,
  Square,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';
import CladexBackground from './components/CladexBackground';

type ViewName = 'relays' | 'workgroups' | 'live';
type ProfileType = 'Claude' | 'Codex';
type RelayType = 'claude' | 'codex';

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
  packaged: boolean;
  appVersion: string;
}

interface ProjectRecord {
  name: string;
  memberCount: number;
  members: Array<{ id: string; displayName: string; relayType: RelayType; workspace: string }>;
  missingMembers: Array<{ name: string; relayType: RelayType }>;
}

interface ProfileFormData {
  name: string;
  type: ProfileType;
  workspace: string;
  discordToken: string;
  channelId: string;
  model?: string;
  triggerMode?: string;
  allowDms?: boolean;
  operatorIds?: string;
  allowedUserIds?: string;
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
  triggerMode: string;
  allowDms: boolean;
  channelId: string;
  operatorIds?: string;
  allowedUserIds: string;
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

const API_BASE = 'http://127.0.0.1:3001/api';
const CLADEX_LOGO = new URL('../assets/icon.png', import.meta.url).href;

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

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.error || 'Request failed');
  }
  return response.json();
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
};

export default function App() {
  const [view, setView] = useState<ViewName>('relays');
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeInfo | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [activeModal, setActiveModal] = useState<'add' | 'edit' | 'logs' | 'settings' | 'workgroup' | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [errorText, setErrorText] = useState('');
  const [bootPending, setBootPending] = useState(true);
  const bootFailureCount = useRef(0);
  const isDark = true;

  useEffect(() => {
    document.documentElement.classList.add('dark');
  }, [isDark]);

  const loadAll = useCallback(async (silent = false) => {
    let keepLoading = false;
    if (!silent) {
      setLoading(true);
    }
    try {
      const [profileRows, projectRows, runtime] = await Promise.all([api.profiles(), api.projects(), api.runtimeInfo()]);
      setProfiles(profileRows);
      setProjects(projectRows);
      setRuntimeInfo(runtime);
      bootFailureCount.current = 0;
      setBootPending(false);
      setErrorText('');
    } catch (error) {
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
      <main className="relative z-10 flex min-h-screen flex-col overflow-y-auto pb-28">
        <header className="mx-auto flex w-full max-w-7xl items-start justify-between gap-6 px-8 pb-2 pt-7">
          <div className="flex items-center gap-4">
            <div className={`relative h-12 w-12 overflow-hidden rounded-[18px] border shadow-[0_0_28px_rgba(99,102,241,0.16)] ${isDark ? 'border-white/10 bg-white/5' : 'border-black/10 bg-white/70 shadow-[0_0_30px_rgba(212,115,94,0.12)]'}`}>
              <img src={CLADEX_LOGO} alt="CLADEX" className="h-full w-full object-cover" />
            </div>
            <div>
              <h1 className={`text-[2.15rem] leading-none font-black tracking-tight ${isDark ? 'text-white' : 'text-slate-900'}`}>ClaDex</h1>
              <p className={`mt-1.5 font-mono text-[11px] uppercase tracking-[0.32em] ${isDark ? 'text-orange-300/85' : 'text-[#b15f4e]'}`}>Unified Relay Network</p>
            </div>
          </div>
          <div className="flex gap-2 self-start pt-4">
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
          ) : (
            <motion.div key="live" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
              <LiveFeed profiles={profiles} selectedProfileId={selectedProfileId} onSelectProfile={setSelectedProfileId} />
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2">
        <div className={`flex items-center gap-2 rounded-2xl border p-2 backdrop-blur-xl shadow-2xl transition-colors duration-500 ${isDark ? 'border-white/10 bg-white/5 shadow-black/50' : 'border-slate-300/70 bg-white/80 shadow-slate-300/50'}`}>
          <DockButton icon={<LayoutGrid />} label="Relays" active={view === 'relays'} onClick={() => setView('relays')} light={!isDark} />
          <DockButton icon={<FolderKanban />} label="Workgroups" active={view === 'workgroups'} onClick={() => setView('workgroups')} light={!isDark} />
          <DockButton icon={<MessageSquare />} label="Live Console" active={view === 'live'} onClick={() => setView('live')} light={!isDark} />
          <div className={`mx-2 h-8 w-px ${isDark ? 'bg-white/10' : 'bg-slate-300/80'}`} />
          <DockButton icon={<Plus />} label="Add Relay" onClick={() => setActiveModal('add')} light={!isDark} />
          <DockButton icon={<Settings />} label="Runtime" onClick={() => setActiveModal('settings')} light={!isDark} />
        </div>
      </div>

      <AnimatePresence>
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
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-8 pb-10 pt-4">
      {loading ? (
        <EmptyState
          title={bootPending ? 'Starting the local CLADEX runtime...' : 'Loading relay state...'}
          detail={bootPending ? 'Waiting for the packaged relay API to become ready.' : 'Refreshing current relay state and active workspaces.'}
          compact={false}
        />
      ) : errorText && profiles.length === 0 ? (
        <EmptyState title="CLADEX could not reach the local relay API." detail="Use Refresh once the packaged backend is up. This is a runtime startup error, not an empty relay list." actionLabel="Refresh" onAction={onRefresh} />
      ) : profiles.length === 0 ? (
        <EmptyState title="No relays configured yet." detail="Choose Add Relay and register a Claude or Codex workspace." />
      ) : (
        <div className="grid auto-rows-fr gap-6 md:grid-cols-2 xl:grid-cols-3">
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
      className="group relative h-[276px] [perspective:1200px]"
    >
      <div className="absolute inset-0 rounded-[32px] bg-black/25 blur-2xl dark:bg-black/35" />
      <div className="relative h-full overflow-hidden rounded-[28px] border border-slate-200/70 bg-white/70 p-5 shadow-[0_18px_44px_rgba(15,23,42,0.12)] backdrop-blur-xl transition-colors duration-500 dark:border-white/10 dark:bg-[#09090b]/90 dark:shadow-2xl">
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#0f172a08_1px,transparent_1px),linear-gradient(to_bottom,#0f172a08_1px,transparent_1px)] bg-[size:24px_24px] opacity-60 dark:bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)]" />
        <motion.div className="pointer-events-none absolute inset-0 rounded-[28px] opacity-0 transition-opacity duration-300 group-hover:opacity-100" style={{ background: spotlight }} />
        <div className="pointer-events-none absolute -right-14 top-8 h-24 w-24 rounded-full blur-3xl" style={{ background: `${accent}28` }} />
        <div className="relative z-10 flex h-full flex-col">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.24em] ${isClaude ? 'border-orange-500/30 bg-orange-500/10 text-orange-700 dark:text-orange-200' : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200'}`}>{profile.type}</div>
            <h3 className="mt-3 text-[1.9rem] leading-none font-bold tracking-tight text-slate-900 dark:text-white">{labelFor(profile)}</h3>
            <p className="mt-2 text-sm text-slate-500 dark:text-gray-400"># {workspaceFor(profile)}</p>
          </div>
          <div className="flex gap-2">
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
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-8 pb-8 pt-8">
      <div className="mb-8 flex items-end justify-between gap-4">
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
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-8 pb-8 pt-8">
      <div className="overflow-hidden rounded-[32px] border border-slate-200/80 bg-[#fbfaf6] shadow-[0_22px_60px_rgba(15,23,42,0.12)] dark:border-white/10 dark:bg-[#0a0a0c] dark:shadow-2xl">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white/60 px-5 py-4 dark:border-white/5 dark:bg-white/[0.03]">
          {workspaces.map((workspace) => (
            <button key={workspace} onClick={() => setActiveWorkspace(workspace)} className={`rounded-2xl px-4 py-2 text-sm font-medium transition-colors ${activeWorkspace === workspace ? 'border border-indigo-500/30 bg-indigo-500/15 text-indigo-700 dark:text-indigo-200' : 'text-slate-500 hover:bg-black/5 hover:text-slate-900 dark:text-gray-500 dark:hover:bg-white/5 dark:hover:text-gray-200'}`}>
              {workspace.split(/[\\/]/).filter(Boolean).pop() || workspace}
            </button>
          ))}
        </div>
        <div className="grid min-h-[640px] grid-cols-1 xl:grid-cols-[260px_minmax(0,1fr)_320px]">
          <div className="border-r border-slate-200 bg-white/30 p-5 dark:border-white/5 dark:bg-black/20">
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

          <div className="border-r border-slate-200 dark:border-white/5">
            <div className="border-b border-slate-200 bg-white/60 px-6 py-5 dark:border-white/5 dark:bg-white/[0.03]">
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
            <div className="flex h-[560px] flex-col bg-[#f7f3ea]/70 dark:bg-black/30">
              <div className="flex-1 overflow-y-auto p-6">
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
                <div className="flex gap-3">
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
                    className="inline-flex min-w-[120px] items-center justify-center gap-2 self-end rounded-[22px] bg-indigo-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {sending ? <Loader2 size={16} className="animate-spin" /> : <MessageSquare size={16} />}
                    Send
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-white/40 p-6 dark:bg-white/[0.03]">
            <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Relay details</div>
            {activeProfile ? (
              <div className="mt-4 space-y-4">
                <InspectorRow label="Relay" value={labelFor(activeProfile)} />
                <InspectorRow label="Workspace" value={workspaceFor(activeProfile)} />
                <InspectorRow label="Worktree" value={activeProfile.activeWorktree || activeProfile.workspace} mono />
                <InspectorRow label="Backend" value={activeProfile.provider || 'Runtime'} />
                <InspectorRow label="Model" value={activeProfile.model || (activeProfile.type === 'Codex' ? 'gpt-5.4' : 'Claude default')} mono />
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
  const [triggerMode, setTriggerMode] = useState('mention_or_dm');
  const [allowDms, setAllowDms] = useState(false);
  const [operatorIds, setOperatorIds] = useState('');
  const [allowedUserIds, setAllowedUserIds] = useState('');
  const [allowedChannelAuthorIds, setAllowedChannelAuthorIds] = useState('');
  const [channelNoMentionAuthorIds, setChannelNoMentionAuthorIds] = useState('');
  const [channelHistoryLimit, setChannelHistoryLimit] = useState('20');
  const [startupDmUserIds, setStartupDmUserIds] = useState('');
  const [startupDmText, setStartupDmText] = useState('Discord relay online. DM me here to chat with Codex.');
  const [startupChannelText, setStartupChannelText] = useState('');
  const [saving, setSaving] = useState(false);

  const codex = type === 'Codex';

  return (
    <ModalShell title="Add Relay" onClose={onClose} wide>
      <div className="space-y-6">
        <div className="grid grid-cols-2 gap-4">
          <TypeButton active={type === 'Claude'} label="Claude Code" icon={<Bot size={18} />} onClick={() => setType('Claude')} tone="orange" />
          <TypeButton active={type === 'Codex'} label="Codex" icon={<Terminal size={18} />} onClick={() => setType('Codex')} tone="emerald" />
        </div>

        <FormSection title="Basics">
          <FormInput label="Bot label" value={name} onChange={setName} placeholder="Tyson" />
          <BrowseField label="Workspace folder" value={workspace} onChange={setWorkspace} placeholder="C:\\Projects\\my-repo" />
          <FormInput label="Discord bot token" value={discordToken} onChange={setDiscordToken} placeholder="Paste token" type="password" />
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Allowed channel IDs" value={channelId} onChange={setChannelId} placeholder="123456789012345678, 234567890123456789" mono />
            <FormInput label="Model override" value={model} onChange={setModel} placeholder={codex ? 'gpt-5.4' : 'claude-opus-4-5-20251101'} mono />
          </div>
        </FormSection>

        <FormSection title="Access">
          <div className="grid gap-4 md:grid-cols-2">
            <FormSelect label="Trigger mode" value={triggerMode} onChange={setTriggerMode} options={[{ value: 'mention_or_dm', label: 'Mention or direct message' }, { value: 'all', label: 'Every message in the channel' }, { value: 'dm_only', label: 'Direct messages only' }]} />
            <FormInput label={codex ? 'Approved DM user IDs' : 'Approved user IDs'} value={allowedUserIds} onChange={setAllowedUserIds} placeholder="Comma-separated Discord user IDs" mono />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Operator IDs" value={operatorIds} onChange={setOperatorIds} placeholder="Comma-separated Discord user IDs" mono />
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

        <div className="flex justify-end gap-3 pt-2">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Save relay'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} onClick={async () => {
            if (!name || !workspace || !discordToken || !channelId) return;
            setSaving(true);
            try {
              await onSubmit({
                name,
                type,
                workspace,
                discordToken,
                channelId,
                model,
                triggerMode,
                allowDms,
                operatorIds,
                allowedUserIds,
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

function EditProfileModal({ profile, onClose, onSubmit }: { profile: Profile; onClose: () => void; onSubmit: (data: ProfileSettingsData) => Promise<void> }) {
  const [workspace, setWorkspace] = useState(profile.workspace);
  const [discordToken, setDiscordToken] = useState('');
  const [botName, setBotName] = useState(profile.botName || profile.displayName || '');
  const [model, setModel] = useState(profile.model || '');
  const [triggerMode, setTriggerMode] = useState(profile.triggerMode || 'mention_or_dm');
  const [allowDms, setAllowDms] = useState(Boolean(profile.allowDms));
  const [channelId, setChannelId] = useState(profile.allowedChannelIds || profile.discordChannel || '');
  const [operatorIds, setOperatorIds] = useState(profile.operatorIds || '');
  const [allowedUserIds, setAllowedUserIds] = useState(profile.allowedUserIds || '');
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
            <FormInput label="Model" value={model} onChange={setModel} placeholder={codex ? 'gpt-5.4' : 'claude-opus-4-5-20251101'} mono />
          </div>
        </FormSection>

        <FormSection title="Access">
          <div className="grid gap-4 md:grid-cols-2">
            <FormSelect label="Trigger mode" value={triggerMode} onChange={setTriggerMode} options={[{ value: 'mention_or_dm', label: 'Mention or direct message' }, { value: 'all', label: 'Every message in the channel' }, { value: 'dm_only', label: 'Direct messages only' }]} />
            <FormInput label={codex ? 'Approved DM user IDs' : 'Approved user IDs'} value={allowedUserIds} onChange={setAllowedUserIds} placeholder="Comma-separated Discord user IDs" mono />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <FormInput label="Operator IDs" value={operatorIds} onChange={setOperatorIds} placeholder="Comma-separated Discord user IDs" mono />
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

        <div className="flex justify-end gap-3 pt-2">
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
                triggerMode,
                allowDms,
                channelId,
                operatorIds,
                allowedUserIds,
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
        <div className="flex justify-end gap-3 pt-2">
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
        <p className="text-sm leading-relaxed text-slate-600 dark:text-gray-400">This panel shows the real desktop runtime. Profile behavior lives with each relay, not in fake global settings.</p>
        <InspectorRow label="API base" value={runtimeInfo?.apiBase || 'Loading...'} mono />
        <InspectorRow label="Backend path" value={runtimeInfo?.backendDir || 'Loading...'} mono />
        <InspectorRow label="App version" value={runtimeInfo?.appVersion || 'Loading...'} />
        <InspectorRow label="Packaging" value={runtimeInfo?.packaged ? 'Packaged desktop build' : 'Source build'} />
        <div className="rounded-2xl border border-slate-200/80 bg-white/70 p-4 text-sm text-slate-600 dark:border-white/10 dark:bg-black/30 dark:text-gray-400">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">Runtime notes</div>
          <ul className="space-y-2">
            <li>Codex stays the deeper runtime because it is app-server based.</li>
            <li>Claude now shares the same durable memory, worktree, status, and handoff path instead of a thin side path.</li>
            <li>Bot labels, trigger mode, model choice, and DM access are managed per relay profile.</li>
          </ul>
        </div>
        <div className="flex justify-end gap-3">
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
  return (
    <label className="block">
      <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500 dark:text-gray-500">{label}</div>
      <div className="flex gap-3">
        <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="min-w-0 flex-1 rounded-2xl border border-slate-200 bg-white/80 px-4 py-3 text-sm text-slate-900 outline-none focus:border-indigo-500 dark:border-white/10 dark:bg-black/40 dark:text-white" />
        <button
          type="button"
          onClick={async () => onChange(await chooseWorkspaceFolder(value))}
          className="rounded-2xl border border-slate-200 bg-white/80 px-4 py-3 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-100 dark:border-white/10 dark:bg-white/[0.03] dark:text-gray-200 dark:hover:bg-white/[0.08]"
        >
          Browse
        </button>
      </div>
    </label>
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

function ModalShell({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto bg-black/60 p-6 pt-12 backdrop-blur-sm" onClick={onClose}><motion.div initial={{ scale: 0.94, y: 18 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.94, y: 18 }} onClick={(event) => event.stopPropagation()} className={`mb-12 w-full overflow-hidden rounded-2xl border border-slate-200/80 bg-[#f8f6f0] shadow-[0_28px_80px_rgba(15,23,42,0.22)] dark:border-white/10 dark:bg-[#0a0a0c] dark:shadow-2xl ${wide ? 'max-w-2xl' : 'max-w-md'}`}><div className="flex items-center justify-between border-b border-slate-200 bg-white/50 px-5 py-4 dark:border-white/5 dark:bg-white/[0.03]"><div><div className="text-[9px] font-bold uppercase tracking-[0.2em] text-slate-500 dark:text-gray-500">CLADEX</div><div className="mt-0.5 text-lg font-semibold text-slate-900 dark:text-white">{title}</div></div><button onClick={onClose} className="rounded-full bg-slate-200/70 p-1.5 text-slate-500 transition-colors hover:bg-slate-300 hover:text-slate-900 dark:bg-white/5 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white"><X size={14} /></button></div><div className="p-5">{children}</div></motion.div></motion.div>;
}

function DockButton({ icon, label, active, onClick, light = false }: { icon: React.ReactNode; label: string; active?: boolean; onClick: () => void; light?: boolean }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  return <div className="group relative"><motion.button ref={ref} onMouseMove={(event) => { if (!ref.current) return; const bounds = ref.current.getBoundingClientRect(); setPosition({ x: (event.clientX - (bounds.left + bounds.width / 2)) * 0.3, y: (event.clientY - (bounds.top + bounds.height / 2)) * 0.3 }); }} onMouseLeave={() => setPosition({ x: 0, y: 0 })} animate={{ x: position.x, y: position.y }} transition={{ type: 'spring', stiffness: 150, damping: 15, mass: 0.1 }} whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.96 }} onClick={onClick} className={`rounded-xl p-3 transition-colors ${active ? 'bg-indigo-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.5)]' : light ? 'text-slate-600 hover:bg-black/5 hover:text-slate-900' : 'text-gray-400 hover:bg-white/10 hover:text-white'}`}>{icon}</motion.button><div className={`pointer-events-none absolute bottom-full left-1/2 mb-3 -translate-x-1/2 whitespace-nowrap rounded-lg border px-3 py-1.5 text-xs font-bold opacity-0 transition-opacity group-hover:opacity-100 ${light ? 'border-slate-200 bg-white text-slate-800 shadow-xl' : 'border-white/10 bg-black/80 text-white'}`}>{label}</div></div>;
}

function ActionButton({ label, icon, onClick, busy = false, tone = 'default', light = false }: { label: string; icon: React.ReactNode; onClick: () => void; busy?: boolean; tone?: 'default' | 'danger'; light?: boolean }) {
  return <button onClick={onClick} disabled={busy} className={`inline-flex items-center gap-2 rounded-2xl border px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${tone === 'danger' ? 'border-red-500/25 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-200' : light ? 'border-slate-300 bg-white text-slate-800 hover:bg-slate-100' : 'border-white/10 bg-white/[0.04] text-white hover:bg-white/[0.08]'}`}>{busy ? <Loader2 size={16} className="animate-spin" /> : icon}{label}</button>;
}

function PrimaryButton({ label, icon, onClick }: { label: string; icon: React.ReactNode; onClick: () => void }) {
  return <button onClick={onClick} className="inline-flex items-center gap-2 rounded-2xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-500">{icon}{label}</button>;
}

function SecondaryButton({ label, onClick }: { label: string; onClick: () => void }) {
  return <button onClick={onClick} className="rounded-2xl border border-slate-200 bg-white/70 px-4 py-2 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-100 dark:border-white/10 dark:bg-white/[0.03] dark:text-gray-300 dark:hover:bg-white/[0.08]">{label}</button>;
}

function MiniIconButton({ label, icon, onClick, tone = 'default' }: { label: string; icon: React.ReactNode; onClick: () => void; tone?: 'default' | 'danger' }) {
  return <button title={label} onClick={onClick} className={`inline-flex h-9 w-9 items-center justify-center rounded-full border transition-colors ${tone === 'danger' ? 'border-red-500/20 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-200' : 'border-slate-200/80 bg-white/70 text-slate-500 hover:bg-slate-200 hover:text-slate-900 dark:border-white/5 dark:bg-white/5 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white'}`}>{icon}</button>;
}
