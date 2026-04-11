import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  Activity,
  Bot,
  FileText,
  FolderKanban,
  LayoutGrid,
  Loader2,
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
}

interface ProfileSettingsData {
  type: ProfileType;
  botName: string;
  model: string;
  triggerMode: string;
  allowDms: boolean;
  channelId: string;
  allowedUserIds: string;
}

const API_BASE = 'http://localhost:3001/api';

function labelFor(profile: Profile): string {
  return profile.displayName || profile.botName || profile.workspaceLabel || humanize(profile.technicalName || profile.name || 'Relay');
}

function workspaceFor(profile: Profile): string {
  return profile.workspaceLabel || profile.workspace.split(/[\\/]/).filter(Boolean).pop() || profile.workspace;
}

function channelFor(profile: Profile): string {
  return profile.channelLabel || (profile.activeChannel ? `Channel ${profile.activeChannel}` : profile.discordChannel ? `Channel ${profile.discordChannel}` : 'Unassigned');
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

  const loadAll = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
    }
    try {
      const [profileRows, projectRows, runtime] = await Promise.all([api.profiles(), api.projects(), api.runtimeInfo()]);
      setProfiles(profileRows);
      setProjects(projectRows);
      setRuntimeInfo(runtime);
      setErrorText('');
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : 'Failed to refresh CLADEX state.');
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, []);

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
    <div className="relative min-h-screen overflow-hidden bg-[#050505] font-sans text-gray-100 selection:bg-indigo-500/30">
      <CladexBackground />
      <div className="pointer-events-none absolute inset-0 z-0 bg-[radial-gradient(circle_at_top,rgba(249,115,22,0.12),transparent_28%),radial-gradient(circle_at_bottom_right,rgba(16,185,129,0.12),transparent_32%)]" />
      <main className="relative z-10 flex h-screen flex-col pb-24">
        <header className="mx-auto flex w-full max-w-7xl items-center justify-between px-8 pt-8">
          <div className="flex items-center gap-4">
            <div className="relative h-14 w-14 overflow-hidden rounded-2xl border border-white/10 bg-white/5 shadow-[0_0_30px_rgba(99,102,241,0.18)]">
              <img src="/cladex.png" alt="CLADEX" className="h-full w-full object-cover" />
            </div>
            <div>
              <h1 className="text-3xl font-black tracking-tight text-white">CLADEX</h1>
              <p className="font-mono text-xs uppercase tracking-[0.28em] text-orange-300/90">Unified Relay Control</p>
              <p className="mt-2 max-w-2xl text-sm text-gray-400">Readable Claude and Codex relay control with the real runtime behind it. No placeholder state. No ugly profile slugs as the main labels.</p>
            </div>
          </div>
          <div className="hidden gap-3 md:flex">
            <ActionButton label="Refresh" icon={<RefreshCw size={16} />} busy={loading} onClick={() => void loadAll()} />
            <ActionButton label="Stop All" icon={<PauseCircle size={16} />} busy={busyKey === 'stop-all'} tone="danger" onClick={() => void runAction('stop-all', api.stopAll)} />
          </div>
        </header>

        {errorText ? <div className="mx-auto mt-4 w-full max-w-7xl rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">{errorText}</div> : null}

        <AnimatePresence mode="wait">
          {view === 'relays' ? (
            <motion.div key="relays" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
              <RelayDashboard
                profiles={profiles}
                loading={loading}
                busyKey={busyKey}
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
        <div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-white/5 p-2 backdrop-blur-xl shadow-2xl shadow-black/50">
          <DockButton icon={<LayoutGrid />} label="Relays" active={view === 'relays'} onClick={() => setView('relays')} />
          <DockButton icon={<FolderKanban />} label="Workgroups" active={view === 'workgroups'} onClick={() => setView('workgroups')} />
          <DockButton icon={<FileText />} label="Live Feed" active={view === 'live'} onClick={() => setView('live')} />
          <div className="mx-2 h-8 w-px bg-white/10" />
          <DockButton icon={<Plus />} label="Add Relay" onClick={() => setActiveModal('add')} />
          <DockButton icon={<Settings />} label="Settings" onClick={() => setActiveModal('settings')} />
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
  busyKey,
  onStart,
  onStop,
  onRestart,
  onDelete,
  onEdit,
  onLogs,
}: {
  profiles: Profile[];
  loading: boolean;
  busyKey: string | null;
  onStart: (profile: Profile) => void;
  onStop: (profile: Profile) => void;
  onRestart: (profile: Profile) => void;
  onDelete: (profile: Profile) => void;
  onEdit: (profile: Profile) => void;
  onLogs: (profile: Profile) => void;
}) {
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-8 pb-8 pt-8">
      <div className="mb-8 grid gap-4 md:grid-cols-3">
        <SummaryPanel label="Configured relays" value={String(profiles.length)} detail="Every saved Claude and Codex bot in one manager." />
        <SummaryPanel label="Running now" value={String(profiles.filter((profile) => profile.running).length)} detail="Relays with live worker processes running." />
        <SummaryPanel label="Ready for traffic" value={String(profiles.filter((profile) => profile.ready).length)} detail="Relays that started cleanly and are ready to answer." />
      </div>

      {loading ? (
        <div className="flex h-[55vh] items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-indigo-300" />
        </div>
      ) : profiles.length === 0 ? (
        <EmptyState title="No relays configured yet." detail="Choose Add Relay and register a Claude or Codex workspace." />
      ) : (
        <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
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
  const tone = isClaude ? 'border-orange-500/25 bg-orange-500/8 text-orange-200' : 'border-emerald-500/25 bg-emerald-500/8 text-emerald-200';

  return (
    <div className="relative overflow-hidden rounded-[30px] border border-white/10 bg-[#0a0a0c] p-6 shadow-2xl">
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:24px_24px] opacity-50" />
      <div className="relative z-10 flex h-full flex-col">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className={`inline-flex rounded-full border px-2 py-1 text-[10px] font-bold uppercase tracking-[0.22em] ${tone}`}>{profile.type}</div>
            <h3 className="mt-3 text-2xl font-bold tracking-tight text-white">{labelFor(profile)}</h3>
            <p className="mt-1 text-sm text-gray-400">{workspaceFor(profile)}</p>
          </div>
          <div className="flex gap-2">
            <MiniIconButton label="Logs" icon={<FileText size={14} />} onClick={onLogs} />
            <MiniIconButton label="Edit" icon={<Pencil size={14} />} onClick={onEdit} />
            <MiniIconButton label="Restart" icon={<RotateCcw size={14} />} onClick={onRestart} />
            <MiniIconButton label="Remove" icon={<Trash2 size={14} />} tone="danger" onClick={onDelete} />
          </div>
        </div>

        <div className="mt-6 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <InfoRow label="Backend" value={profile.provider || 'Relay runtime'} mono />
          <InfoRow label="Model" value={profile.model || (profile.type === 'Codex' ? 'gpt-5.4' : 'Claude default')} mono />
          <InfoRow label="Trigger" value={profile.triggerMode || 'Mention or direct message'} />
          <InfoRow label="Direct messages" value={profile.allowDms ? 'Enabled' : 'Disabled'} />
          <InfoRow label="Channel" value={channelFor(profile)} />
        </div>

        <div className="mt-4 rounded-2xl border border-white/5 bg-black/30 p-4 text-sm leading-relaxed text-gray-300">
          {profile.statusText || 'Ready for the next Discord turn.'}
        </div>

        <div className="mt-auto flex items-center justify-between pt-5">
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <span className={`h-2.5 w-2.5 rounded-full ${running ? (isClaude ? 'bg-orange-400' : 'bg-emerald-400') : 'bg-gray-600'} ${running ? 'animate-pulse' : ''}`} />
            {running ? (profile.state === 'working' ? 'Working' : 'Listening') : 'Stopped'}
          </div>
          <button
            onClick={running ? onStop : onStart}
            disabled={busy}
            className={`inline-flex items-center gap-2 rounded-2xl border px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${
              running
                ? 'border-red-500/30 bg-red-500/10 text-red-200 hover:bg-red-500/20'
                : isClaude
                  ? 'border-orange-500/30 bg-orange-500/10 text-orange-200 hover:bg-orange-500/20'
                  : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20'
            }`}
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : running ? <Square size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
            {running ? 'Stop' : 'Start'}
          </button>
        </div>
      </div>
    </div>
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
          <div className="text-[10px] font-bold uppercase tracking-[0.24em] text-gray-500">Saved workgroups</div>
          <h2 className="mt-2 text-3xl font-black tracking-tight text-white">Start or stop related relays together.</h2>
          <p className="mt-2 max-w-2xl text-sm text-gray-400">This replaces the old project strip with a real workgroup surface in the desktop app.</p>
        </div>
        <ActionButton label="New Workgroup" icon={<Plus size={16} />} onClick={onCreate} />
      </div>

      {projects.length === 0 ? (
        <EmptyState title="No workgroups saved yet." detail="Create a group from the relays you already have registered." />
      ) : (
        <div className="grid gap-5 lg:grid-cols-2">
          {projects.map((project) => (
            <div key={project.name} className="rounded-[30px] border border-white/10 bg-white/[0.03] p-6 shadow-2xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Workgroup</div>
                  <div className="mt-2 text-2xl font-bold tracking-tight text-white">{project.name}</div>
                  <div className="mt-2 text-sm text-gray-400">{project.memberCount} relay{project.memberCount === 1 ? '' : 's'}</div>
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
                    <div key={`${member.relayType}:${member.id}`} className="flex items-center justify-between rounded-2xl border border-white/5 bg-black/30 px-4 py-3">
                      <div>
                        <div className="text-sm font-semibold text-white">{member.displayName}</div>
                        <div className="text-xs text-gray-500">{profile ? workspaceFor(profile) : member.workspace}</div>
                      </div>
                      <div className={`rounded-full px-2 py-1 text-[10px] font-bold uppercase tracking-[0.22em] ${member.relayType === 'claude' ? 'bg-orange-500/10 text-orange-200' : 'bg-emerald-500/10 text-emerald-200'}`}>
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
  const [logs, setLogs] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
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
    const loadLogs = async () => {
      if (!activeProfile) {
        setLogs([]);
        return;
      }
      setLoading(true);
      try {
        const payload = await api.logs(activeProfile.id, activeProfile.relayType);
        if (!cancelled) {
          setLogs(payload.logs || []);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    void loadLogs();
    const interval = window.setInterval(() => void loadLogs(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeProfile]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-8 pb-8 pt-8">
      <div className="overflow-hidden rounded-[32px] border border-white/10 bg-[#0a0a0c] shadow-2xl">
        <div className="flex flex-wrap items-center gap-2 border-b border-white/5 bg-white/[0.03] px-5 py-4">
          {workspaces.map((workspace) => (
            <button key={workspace} onClick={() => setActiveWorkspace(workspace)} className={`rounded-2xl px-4 py-2 text-sm font-medium transition-colors ${activeWorkspace === workspace ? 'border border-indigo-500/30 bg-indigo-500/15 text-indigo-200' : 'text-gray-500 hover:bg-white/5 hover:text-gray-200'}`}>
              {workspace.split(/[\\/]/).filter(Boolean).pop() || workspace}
            </button>
          ))}
        </div>
        <div className="grid min-h-[640px] grid-cols-1 xl:grid-cols-[260px_minmax(0,1fr)_320px]">
          <div className="border-r border-white/5 bg-black/20 p-5">
            <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Relays in this workspace</div>
            <div className="mt-4 space-y-3">
              {workspaceProfiles.map((profile) => (
                <button key={profile.id} onClick={() => onSelectProfile(profile.id)} className={`w-full rounded-2xl border px-4 py-3 text-left transition-colors ${activeProfile?.id === profile.id ? 'border-indigo-500/30 bg-indigo-500/10' : 'border-white/5 bg-white/[0.02] hover:bg-white/[0.06]'}`}>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-white">{labelFor(profile)}</div>
                      <div className="text-xs text-gray-500">{profile.type}</div>
                    </div>
                    <span className={`h-2.5 w-2.5 rounded-full ${profile.running ? (profile.type === 'Claude' ? 'bg-orange-400' : 'bg-emerald-400') : 'bg-gray-600'}`} />
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="border-r border-white/5">
            <div className="border-b border-white/5 bg-white/[0.03] px-6 py-5">
              {activeProfile ? (
                <>
                  <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Live relay feed</div>
                  <div className="mt-2 text-2xl font-bold tracking-tight text-white">{labelFor(activeProfile)}</div>
                  <div className="mt-2 text-sm text-gray-400">{activeProfile.statusText || 'Watching the actual relay log stream for this bot.'}</div>
                </>
              ) : (
                <div className="text-gray-500">Select a relay to inspect it.</div>
              )}
            </div>
            <div className="h-[560px] overflow-y-auto bg-black/30 p-6 font-mono text-xs text-gray-300">
              {!activeProfile ? (
                <EmptyState title="No relay selected." detail="Pick a relay on the left to inspect its feed." compact />
              ) : loading && !logs.length ? (
                <div className="flex items-center gap-2 text-indigo-300"><Loader2 size={16} className="animate-spin" /> Loading relay feed...</div>
              ) : logs.length ? (
                <div className="space-y-2">
                  {logs.map((line, index) => (
                    <div key={`${activeProfile.id}-${index}-${line.slice(0, 12)}`} className="rounded-2xl border border-white/5 bg-white/[0.03] px-4 py-3 leading-relaxed">
                      <span className="mr-3 text-gray-500">{String(index + 1).padStart(2, '0')}</span>
                      <span className={line.toLowerCase().includes('error') ? 'text-red-200' : line.toLowerCase().includes('working') ? 'text-indigo-200' : 'text-gray-200'}>{line}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState title="No log lines yet." detail="Once the relay starts working, the feed will appear here." compact />
              )}
            </div>
          </div>

          <div className="bg-white/[0.03] p-6">
            <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Relay details</div>
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
              <div className="mt-4 text-sm text-gray-500">No relay selected.</div>
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
  const [saving, setSaving] = useState(false);

  return (
    <ModalShell title="Add Relay" onClose={onClose} wide>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <TypeButton active={type === 'Claude'} label="Claude Code" icon={<Bot size={18} />} onClick={() => setType('Claude')} tone="orange" />
          <TypeButton active={type === 'Codex'} label="Codex" icon={<Terminal size={18} />} onClick={() => setType('Codex')} tone="emerald" />
        </div>
        <FormInput label="Bot label" value={name} onChange={setName} placeholder="Tyson" />
        <FormInput label="Workspace folder" value={workspace} onChange={setWorkspace} placeholder="C:\\Projects\\my-repo" mono />
        <FormInput label="Discord bot token" value={discordToken} onChange={setDiscordToken} placeholder="Paste token" type="password" />
        <FormInput label="Allowed channel ID" value={channelId} onChange={setChannelId} placeholder="123456789012345678" mono />
        <div className="grid gap-4 md:grid-cols-2">
          <FormInput label="Model override" value={model} onChange={setModel} placeholder={type === 'Claude' ? 'claude-opus-4-5-20251101' : 'gpt-5.4'} mono />
          <FormSelect label="Trigger mode" value={triggerMode} onChange={setTriggerMode} options={[{ value: 'mention_or_dm', label: 'Mention or direct message' }, { value: 'all', label: 'Every message in the channel' }, { value: 'dm_only', label: 'Direct messages only' }]} />
        </div>
        <FormInput label="Operator IDs" value={operatorIds} onChange={setOperatorIds} placeholder="Comma-separated Discord user IDs" mono />
        <FormInput label="Additional allowed user IDs" value={allowedUserIds} onChange={setAllowedUserIds} placeholder="Comma-separated Discord user IDs" mono />
        <ToggleRow checked={allowDms} onChange={setAllowDms} label="Allow direct messages for approved users" />
        <div className="flex justify-end gap-3 pt-2">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Save relay'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} onClick={async () => {
            if (!name || !workspace || !discordToken || !channelId) return;
            setSaving(true);
            try {
              await onSubmit({ name, type, workspace, discordToken, channelId, model, triggerMode, allowDms, operatorIds, allowedUserIds });
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
  const [botName, setBotName] = useState(profile.botName || profile.displayName || '');
  const [model, setModel] = useState(profile.model || '');
  const [triggerMode, setTriggerMode] = useState(profile.triggerMode || 'mention_or_dm');
  const [allowDms, setAllowDms] = useState(Boolean(profile.allowDms));
  const [channelId, setChannelId] = useState(profile.discordChannel || '');
  const [allowedUserIds, setAllowedUserIds] = useState('');
  const [saving, setSaving] = useState(false);

  return (
    <ModalShell title={`Edit ${labelFor(profile)}`} onClose={onClose} wide>
      <div className="space-y-4">
        <InspectorRow label="Relay type" value={profile.type} />
        <InspectorRow label="Workspace" value={profile.workspace} mono />
        <FormInput label="Bot label" value={botName} onChange={setBotName} placeholder="Tyson" />
        <div className="grid gap-4 md:grid-cols-2">
          <FormInput label="Model" value={model} onChange={setModel} placeholder={profile.type === 'Claude' ? 'claude-opus-4-5-20251101' : 'gpt-5.4'} mono />
          <FormSelect label="Trigger mode" value={triggerMode} onChange={setTriggerMode} options={[{ value: 'mention_or_dm', label: 'Mention or direct message' }, { value: 'all', label: 'Every message in the channel' }, { value: 'dm_only', label: 'Direct messages only' }]} />
        </div>
        <FormInput label="Allowed channel ID" value={channelId} onChange={setChannelId} placeholder="123456789012345678" mono />
        <FormInput label="Allowed user IDs" value={allowedUserIds} onChange={setAllowedUserIds} placeholder="Comma-separated Discord user IDs" mono />
        <ToggleRow checked={allowDms} onChange={setAllowDms} label="Allow direct messages for approved users" />
        <div className="flex justify-end gap-3 pt-2">
          <SecondaryButton label="Cancel" onClick={onClose} />
          <PrimaryButton label={saving ? 'Saving...' : 'Save changes'} icon={saving ? <Loader2 size={16} className="animate-spin" /> : <Pencil size={16} />} onClick={async () => {
            setSaving(true);
            try {
              await onSubmit({ type: profile.type, botName, model, triggerMode, allowDms, channelId, allowedUserIds });
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
        <p className="text-sm leading-relaxed text-gray-400">This panel shows the real desktop runtime. Profile behavior lives with each relay, not in fake global settings.</p>
        <InspectorRow label="API base" value={runtimeInfo?.apiBase || 'Loading...'} mono />
        <InspectorRow label="Backend path" value={runtimeInfo?.backendDir || 'Loading...'} mono />
        <InspectorRow label="App version" value={runtimeInfo?.appVersion || 'Loading...'} />
        <InspectorRow label="Packaging" value={runtimeInfo?.packaged ? 'Packaged desktop build' : 'Source build'} />
        <div className="rounded-2xl border border-white/10 bg-black/30 p-4 text-sm text-gray-400">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">Runtime notes</div>
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

function SummaryPanel({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 shadow-2xl"><div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">{label}</div><div className="mt-2 text-3xl font-black text-white">{value}</div><div className="mt-2 text-sm text-gray-400">{detail}</div></div>;
}

function EmptyState({ title, detail, compact = false }: { title: string; detail: string; compact?: boolean }) {
  return <div className={`flex flex-col items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] px-6 text-center ${compact ? 'h-48 py-8' : 'h-64 py-12'}`}><Activity size={compact ? 28 : 40} className="mb-4 text-gray-600" /><div className="text-lg font-semibold text-white">{title}</div><div className="mt-2 max-w-xl text-sm text-gray-500">{detail}</div></div>;
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3 py-1"><div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">{label}</div><div className={`text-sm text-gray-300 ${mono ? 'font-mono' : ''}`}>{value}</div></div>;
}

function InspectorRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="grid grid-cols-[110px_minmax(0,1fr)] gap-3"><div className="pt-1 text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">{label}</div><div className={`rounded-2xl border border-white/5 bg-black/30 px-3 py-2 text-sm text-gray-200 ${mono ? 'break-all font-mono' : ''}`}>{value}</div></div>;
}

function FormInput({ label, value, onChange, placeholder, mono = false, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; placeholder: string; mono?: boolean; type?: string }) {
  return <label className="block"><div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">{label}</div><input type={type} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className={`w-full rounded-2xl border border-white/10 bg-black/40 px-4 py-3 text-white outline-none focus:border-indigo-500 ${mono ? 'font-mono text-sm' : 'text-sm'}`} /></label>;
}

function FormSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return <label className="block"><div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">{label}</div><select value={value} onChange={(event) => onChange(event.target.value)} className="w-full rounded-2xl border border-white/10 bg-black/40 px-4 py-3 text-sm text-white outline-none focus:border-indigo-500">{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
}

function ToggleRow({ checked, onChange, label }: { checked: boolean; onChange: (checked: boolean) => void; label: string }) {
  return <label className="flex items-center gap-3 rounded-2xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-gray-300"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4 accent-indigo-500" />{label}</label>;
}

function TypeButton({ active, label, icon, onClick, tone }: { active: boolean; label: string; icon: React.ReactNode; onClick: () => void; tone: 'orange' | 'emerald' }) {
  const activeStyles = tone === 'orange' ? 'border-orange-500/40 bg-orange-500/10 text-orange-200' : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200';
  return <button onClick={onClick} className={`flex items-center justify-center gap-3 rounded-2xl border px-4 py-4 font-semibold transition-colors ${active ? activeStyles : 'border-white/10 bg-white/[0.03] text-gray-400 hover:bg-white/[0.06]'}`}>{icon}{label}</button>;
}

function ModalShell({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}><motion.div initial={{ scale: 0.94, y: 18 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.94, y: 18 }} onClick={(event) => event.stopPropagation()} className={`w-full overflow-hidden rounded-[32px] border border-white/10 bg-[#0a0a0c] shadow-2xl ${wide ? 'max-w-3xl' : 'max-w-xl'}`}><div className="flex items-center justify-between border-b border-white/5 bg-white/[0.03] px-6 py-5"><div><div className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-500">CLADEX</div><div className="mt-1 text-xl font-semibold text-white">{title}</div></div><button onClick={onClose} className="rounded-full bg-white/5 p-2 text-gray-400 transition-colors hover:bg-white/10 hover:text-white"><X size={16} /></button></div><div className="p-6">{children}</div></motion.div></motion.div>;
}

function DockButton({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active?: boolean; onClick: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  return <div className="group relative"><motion.button ref={ref} onMouseMove={(event) => { if (!ref.current) return; const bounds = ref.current.getBoundingClientRect(); setPosition({ x: (event.clientX - (bounds.left + bounds.width / 2)) * 0.3, y: (event.clientY - (bounds.top + bounds.height / 2)) * 0.3 }); }} onMouseLeave={() => setPosition({ x: 0, y: 0 })} animate={{ x: position.x, y: position.y }} transition={{ type: 'spring', stiffness: 150, damping: 15, mass: 0.1 }} whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.96 }} onClick={onClick} className={`rounded-xl p-3 transition-colors ${active ? 'bg-indigo-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.5)]' : 'text-gray-400 hover:bg-white/10 hover:text-white'}`}>{icon}</motion.button><div className="pointer-events-none absolute bottom-full left-1/2 mb-3 -translate-x-1/2 whitespace-nowrap rounded-lg border border-white/10 bg-black/80 px-3 py-1.5 text-xs font-bold text-white opacity-0 transition-opacity group-hover:opacity-100">{label}</div></div>;
}

function ActionButton({ label, icon, onClick, busy = false, tone = 'default' }: { label: string; icon: React.ReactNode; onClick: () => void; busy?: boolean; tone?: 'default' | 'danger' }) {
  return <button onClick={onClick} disabled={busy} className={`inline-flex items-center gap-2 rounded-2xl border px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${tone === 'danger' ? 'border-red-500/25 bg-red-500/10 text-red-200 hover:bg-red-500/20' : 'border-white/10 bg-white/[0.04] text-white hover:bg-white/[0.08]'}`}>{busy ? <Loader2 size={16} className="animate-spin" /> : icon}{label}</button>;
}

function PrimaryButton({ label, icon, onClick }: { label: string; icon: React.ReactNode; onClick: () => void }) {
  return <button onClick={onClick} className="inline-flex items-center gap-2 rounded-2xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-500">{icon}{label}</button>;
}

function SecondaryButton({ label, onClick }: { label: string; onClick: () => void }) {
  return <button onClick={onClick} className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-2 text-sm font-semibold text-gray-300 transition-colors hover:bg-white/[0.08]">{label}</button>;
}

function MiniIconButton({ label, icon, onClick, tone = 'default' }: { label: string; icon: React.ReactNode; onClick: () => void; tone?: 'default' | 'danger' }) {
  return <button title={label} onClick={onClick} className={`rounded-full p-2 transition-colors ${tone === 'danger' ? 'bg-red-500/10 text-red-200 hover:bg-red-500/20' : 'bg-white/5 text-gray-400 hover:bg-white/10 hover:text-white'}`}>{icon}</button>;
}
