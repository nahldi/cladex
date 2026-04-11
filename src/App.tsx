import React, { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence, useMotionValue, useTransform, useSpring, useMotionTemplate } from 'motion/react';
import {
  Terminal, Bot, Hash, Activity, Play, Square, Settings, X,
  Send, Plus, FileText, Trash2,
  MessageSquare, LayoutGrid, RefreshCw, Loader2
} from 'lucide-react';
import CladexBackground from './components/CladexBackground';

// --- Types ---

type ProfileType = 'Claude' | 'Codex';
type ProfileStatus = 'Running' | 'Stopped';
type AgentState = 'idle' | 'working';

interface Profile {
  id: string;
  name: string;
  type: ProfileType;
  relayType?: 'claude' | 'codex';
  workspace: string;
  status: ProfileStatus;
  discordChannel: string;
  state: AgentState;
  ready?: boolean;
  provider?: string;
  model?: string;
  triggerMode?: string;
  effort?: string;
  botName?: string;
  allowDms?: boolean;
  stateNamespace?: string;
  statusText?: string;
  sessionId?: string;
  activeWorktree?: string;
  activeChannel?: string;
  logPath?: string;
}

interface RuntimeInfo {
  apiBase: string;
  backendDir: string;
  packaged: boolean;
  appVersion: string;
}

const API_BASE = 'http://localhost:3001/api';

// --- API Functions ---

async function fetchProfiles(): Promise<Profile[]> {
  try {
    const res = await fetch(`${API_BASE}/profiles`);
    if (!res.ok) throw new Error('Failed to fetch');
    return await res.json();
  } catch {
    return [];
  }
}

async function fetchStatus(): Promise<{ running: string[] }> {
  try {
    const res = await fetch(`${API_BASE}/status`);
    if (!res.ok) throw new Error('Failed to fetch');
    return await res.json();
  } catch {
    return { running: [] };
  }
}

async function startRelay(id: string, type: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/profiles/${id}/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: type.toLowerCase() })
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function stopRelay(id: string, type: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/profiles/${id}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: type.toLowerCase() })
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function deleteProfile(id: string, type: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/profiles/${id}?type=${type.toLowerCase()}`, {
      method: 'DELETE'
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function createProfile(data: {
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
}): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/profiles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function fetchLogs(id: string, type: string): Promise<string[]> {
  try {
    const res = await fetch(`${API_BASE}/profiles/${id}/logs?type=${type.toLowerCase()}`);
    if (!res.ok) throw new Error('Failed to fetch');
    const data = await res.json();
    return data.logs || [];
  } catch {
    return [];
  }
}

async function fetchRuntimeInfo(): Promise<RuntimeInfo | null> {
  try {
    const res = await fetch(`${API_BASE}/runtime-info`);
    if (!res.ok) throw new Error('Failed to fetch');
    return await res.json();
  } catch {
    return null;
  }
}

// --- Main App Component ---

export default function App() {
  const [view, setView] = useState<'dashboard' | 'chat'>('dashboard');
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Modals
  const [activeModal, setActiveModal] = useState<'add' | 'settings' | 'logs' | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);

  // Mouse tracking for global effects
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });

  const loadProfiles = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoading(true);
    }
    const data = await fetchProfiles();
    setProfiles(data);
    if (!options?.silent) {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProfiles();
    const refreshInterval = window.setInterval(() => {
      void loadProfiles({ silent: true });
    }, 5000);

    const handleMouseMove = (e: MouseEvent) => {
      setMousePos({ x: e.clientX, y: e.clientY });
    };
    window.addEventListener('mousemove', handleMouseMove);
    return () => {
      window.clearInterval(refreshInterval);
      window.removeEventListener('mousemove', handleMouseMove);
    };
  }, [loadProfiles]);

  const toggleStatus = async (id: string) => {
    const profile = profiles.find(p => p.id === id);
    if (!profile) return;

    setActionLoading(id);
    const isRunning = profile.status === 'Running';

    const success = isRunning
      ? await stopRelay(id, profile.type)
      : await startRelay(id, profile.type);

    if (success) {
      setProfiles(profiles.map(p =>
        p.id === id
          ? { ...p, status: isRunning ? 'Stopped' : 'Running', state: isRunning ? 'idle' : 'working' }
          : p
      ));
    }
    setActionLoading(null);
  };

  const handleDelete = async (id: string) => {
    const profile = profiles.find(p => p.id === id);
    if (!profile) return;

    setActionLoading(id);
    const success = await deleteProfile(id, profile.type);
    if (success) {
      setProfiles(profiles.filter(p => p.id !== id));
    }
    setActionLoading(null);
  };

  return (
    <div className="relative min-h-screen bg-[#050505] text-gray-100 font-sans overflow-hidden selection:bg-indigo-500/30">
      <CladexBackground />

      {/* Interactive Ambient Glow */}
      <motion.div
        className="pointer-events-none fixed inset-0 z-0 opacity-40"
        animate={{
          background: `radial-gradient(circle 600px at ${mousePos.x}px ${mousePos.y}px, rgba(99, 102, 241, 0.15), transparent 80%)`
        }}
        transition={{ type: 'tween', ease: 'backOut', duration: 0.5 }}
      />

      <div className="absolute inset-0 z-0 pointer-events-none bg-[radial-gradient(circle_at_top,rgba(249,115,22,0.1),transparent_28%),radial-gradient(circle_at_bottom_right,rgba(16,185,129,0.1),transparent_32%)]"></div>

      {/* Main Content Area */}
      <main className="relative z-10 h-screen flex flex-col pb-24">
        <AnimatePresence mode="wait">
          {view === 'dashboard' ? (
            <motion.div key="dashboard">
              <DashboardView
                profiles={profiles}
                loading={loading}
                actionLoading={actionLoading}
                onToggle={toggleStatus}
                onDelete={handleDelete}
                onRefresh={loadProfiles}
                onOpenLogs={(id) => { setSelectedProfileId(id); setActiveModal('logs'); }}
              />
            </motion.div>
          ) : (
            <motion.div key="chat">
              <ChatView
                profiles={profiles}
                onRefresh={() => { void loadProfiles({ silent: true }); }}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      {/* Floating Dock */}
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
        <div className="flex items-center gap-2 p-2 rounded-2xl bg-white/5 border border-white/10 backdrop-blur-xl shadow-2xl shadow-black/50">
          <DockButton icon={<LayoutGrid />} label="ClaDex" active={view === 'dashboard'} onClick={() => setView('dashboard')} />
          <DockButton icon={<MessageSquare />} label="Live Feed" active={view === 'chat'} onClick={() => setView('chat')} />
          <div className="w-px h-8 bg-white/10 mx-2" />
          <DockButton icon={<Plus />} label="Add Relay" onClick={() => setActiveModal('add')} />
          <DockButton icon={<Settings />} label="Settings" onClick={() => setActiveModal('settings')} />
        </div>
      </div>

      {/* Modals */}
      <AnimatePresence>
        {activeModal === 'add' && <AddProfileModal onClose={() => setActiveModal(null)} onAdd={async (data) => {
          const success = await createProfile(data);
          if (success) {
            await loadProfiles();
          }
          setActiveModal(null);
        }} />}
        {activeModal === 'settings' && <SettingsModal onClose={() => setActiveModal(null)} />}
        {activeModal === 'logs' && selectedProfileId && (
          <LogsModal
            profile={profiles.find(p => p.id === selectedProfileId)!}
            onClose={() => setActiveModal(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

// --- Dashboard View ---

function DashboardView({ profiles, loading, actionLoading, onToggle, onDelete, onRefresh, onOpenLogs }: {
  profiles: Profile[];
  loading: boolean;
  actionLoading: string | null;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onRefresh: () => void;
  onOpenLogs: (id: string) => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20, filter: 'blur(10px)' }}
      className="flex-1 overflow-y-auto p-8 max-w-7xl mx-auto w-full"
    >
      <header className="mb-12 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="relative h-14 w-14 overflow-hidden rounded-2xl border border-white/10 bg-white/5 shadow-[0_0_30px_rgba(99,102,241,0.18)]">
            <img src="/cladex.jpg" alt="CLADEX" className="h-full w-full object-cover" />
            <div className="absolute inset-0 rounded-2xl ring-1 ring-inset ring-white/10" />
          </div>
          <div>
            <h1 className="text-3xl font-black tracking-tighter bg-clip-text text-transparent bg-gradient-to-r from-white to-gray-500 relative group cursor-default">
              CLADEX
              <span className="absolute inset-0 bg-clip-text text-transparent bg-gradient-to-r from-indigo-500 to-purple-500 opacity-0 group-hover:opacity-100 group-hover:animate-pulse transition-opacity duration-300 -translate-x-[1px] translate-y-[1px]">CLADEX</span>
              <span className="absolute inset-0 bg-clip-text text-transparent bg-gradient-to-r from-red-500 to-orange-500 opacity-0 group-hover:opacity-100 group-hover:animate-pulse transition-opacity duration-300 translate-x-[1px] -translate-y-[1px]" style={{ animationDelay: '50ms' }}>CLADEX</span>
            </h1>
            <p className="font-mono text-xs uppercase tracking-[0.28em] text-orange-300/90">Unified Relay Control</p>
            <p className="mt-2 max-w-xl text-sm text-gray-400">Real Codex and Claude relay control, live operator visibility, and readable runtime state without placeholder telemetry.</p>
          </div>
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="p-3 rounded-xl bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors disabled:opacity-50"
        >
          <RefreshCw size={20} className={loading ? 'animate-spin' : ''} />
        </button>
      </header>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-indigo-400" />
        </div>
      ) : profiles.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-64 text-gray-500">
          <Bot size={48} className="mb-4 opacity-50" />
          <p>No relay profiles configured yet.</p>
          <p className="text-sm">Choose Add Relay and register a Claude or Codex workspace.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {profiles.map((profile, i) => (
            <React.Fragment key={profile.id}>
              <InteractiveCard
                profile={profile}
                index={i}
                loading={actionLoading === profile.id}
                onToggle={() => onToggle(profile.id)}
                onDelete={() => onDelete(profile.id)}
                onOpenLogs={() => onOpenLogs(profile.id)}
              />
            </React.Fragment>
          ))}
        </div>
      )}
    </motion.div>
  );
}

function InteractiveCard({ profile, index, loading, onToggle, onDelete, onOpenLogs }: {
  profile: Profile;
  index: number;
  loading: boolean;
  onToggle: () => void;
  onDelete: () => void;
  onOpenLogs: () => void;
}) {
  const isRunning = profile.status === 'Running';
  const isClaude = profile.type === 'Claude';
  const colorHex = isClaude ? '#f97316' : '#10b981';

  // 3D Tilt & Spotlight Effect
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);

  const mouseXSpring = useSpring(x);
  const mouseYSpring = useSpring(y);
  const rotateX = useTransform(mouseYSpring, [-0.5, 0.5], ["10deg", "-10deg"]);
  const rotateY = useTransform(mouseXSpring, [-0.5, 0.5], ["-10deg", "10deg"]);

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;
    const mX = e.clientX - rect.left;
    const mY = e.clientY - rect.top;

    mouseX.set(mX);
    mouseY.set(mY);

    const xPct = mX / width - 0.5;
    const yPct = mY / height - 0.5;
    x.set(xPct);
    y.set(yPct);
  };

  const handleMouseLeave = () => {
    x.set(0);
    y.set(0);
  };

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: index * 0.1, type: 'spring', stiffness: 200, damping: 20 }}
      style={{ perspective: 1000 }}
      className="relative group h-[280px]"
    >
      <motion.div
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        style={{ rotateX, rotateY, transformStyle: "preserve-3d" }}
        className={`w-full h-full rounded-3xl border border-white/10 bg-[#0a0a0c] p-6 flex flex-col justify-between relative overflow-hidden shadow-2xl transition-shadow duration-300 ${
          isRunning ? `hover:shadow-[0_0_40px_${colorHex}40]` : 'hover:shadow-[0_0_20px_rgba(255,255,255,0.1)]'
        }`}
      >
        {/* Animated Background Grid */}
        <div className={`absolute inset-0 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:24px_24px] [transform:translateZ(-50px)] opacity-50 ${isRunning ? 'animate-[scroll-bg_2s_linear_infinite]' : ''}`}></div>

        {/* Spotlight Hover Effect */}
        <motion.div
          className="pointer-events-none absolute -inset-px rounded-3xl opacity-0 transition duration-300 group-hover:opacity-100 z-20"
          style={{
            background: useMotionTemplate`radial-gradient(400px circle at ${mouseX}px ${mouseY}px, ${isClaude ? 'rgba(249, 115, 22, 0.15)' : 'rgba(16, 185, 129, 0.15)'}, transparent 40%)`
          }}
        />

        {/* Top: Header & Controls */}
        <div className="flex justify-between items-start relative z-10" style={{ transform: "translateZ(30px)" }}>
          <div>
            <div className={`text-[10px] font-bold uppercase tracking-widest mb-1 ${isClaude ? 'text-orange-400' : 'text-emerald-400'}`}>
              {profile.type}
            </div>
            <h3 className="text-xl font-bold text-white tracking-tight">{profile.name}</h3>
            <div className="flex items-center gap-2 mt-1 text-xs text-gray-500 font-mono">
              <Hash size={12} /> {profile.workspace}
            </div>
          </div>

          <div className="flex gap-2">
            <button onClick={onOpenLogs} className="p-2 rounded-full bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors">
              <FileText size={14} />
            </button>
            <button onClick={onDelete} disabled={loading} className="p-2 rounded-full bg-white/5 hover:bg-red-500/20 text-gray-400 hover:text-red-400 transition-colors disabled:opacity-50">
              <Trash2 size={14} />
            </button>
          </div>
        </div>

        {/* Middle: The Connection Visualization */}
        <div className="flex-1 flex items-center justify-center relative z-10 my-4" style={{ transform: "translateZ(40px)" }}>
          <div className="flex items-center w-full max-w-[200px] justify-between relative">
            {/* AI Node */}
            <div className={`relative z-10 h-10 w-10 rounded-xl flex items-center justify-center bg-[#0a0a0c] border-2 ${isRunning ? (isClaude ? 'border-orange-500 shadow-[0_0_15px_rgba(249,115,22,0.5)]' : 'border-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.5)]') : 'border-gray-700'}`}>
              {isClaude ? <Bot size={18} className={isRunning ? 'text-orange-400' : 'text-gray-600'} /> : <Terminal size={18} className={isRunning ? 'text-emerald-400' : 'text-gray-600'} />}
            </div>

            {/* Animated Line */}
            <div className="absolute left-10 right-10 h-[2px] bg-gray-800 overflow-hidden">
              {isRunning && (
                <motion.div
                  className={`h-full w-1/2 ${isClaude ? 'bg-gradient-to-r from-transparent via-orange-500 to-transparent' : 'bg-gradient-to-r from-transparent via-emerald-500 to-transparent'}`}
                  animate={{ x: ['-100%', '200%'] }}
                  transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
                />
              )}
            </div>

            {/* Discord Node */}
            <div className={`relative z-10 h-10 w-10 rounded-xl flex items-center justify-center bg-[#0a0a0c] border-2 ${isRunning ? 'border-[#5865F2] shadow-[0_0_15px_#5865F280]' : 'border-gray-700'}`}>
              <Hash size={18} className={isRunning ? 'text-[#5865F2]' : 'text-gray-600'} />
            </div>
          </div>
        </div>

        {/* Bottom: Status & Toggle */}
        <div className="flex items-center justify-between relative z-10" style={{ transform: "translateZ(20px)" }}>
          <div className="flex flex-col">
            <span className="text-[10px] text-gray-500 uppercase tracking-wider font-bold">Status</span>
            <span className={`text-sm font-medium flex items-center gap-2 ${isRunning ? (isClaude ? 'text-orange-400' : 'text-emerald-400') : 'text-gray-500'}`}>
              {isRunning ? (
                <>
                  <span className={`w-2 h-2 rounded-full ${isClaude ? 'bg-orange-500' : 'bg-emerald-500'} animate-pulse`} />
                  {profile.state === 'working' ? 'Working...' : 'Listening'}
                </>
              ) : (
                <>
                  <span className="w-2 h-2 rounded-full bg-gray-600" /> Offline
                </>
              )}
            </span>
          </div>

          <button
            onClick={onToggle}
            disabled={loading}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl font-bold text-sm transition-all disabled:opacity-50 ${
              isRunning
                ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/30'
                : isClaude
                  ? 'bg-orange-500/10 text-orange-400 hover:bg-orange-500/20 border border-orange-500/30'
                  : 'bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border border-emerald-500/30'
            }`}
          >
            {loading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : isRunning ? (
              <><Square size={14} fill="currentColor" /> Stop</>
            ) : (
              <><Play size={14} fill="currentColor" /> Start</>
            )}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

// --- Chatroom View ---

function ChatView({ profiles, onRefresh }: { profiles: Profile[]; onRefresh: () => void }) {
  const workspaces = Array.from(new Set(profiles.map(p => p.workspace))).sort();
  const [activeWorkspace, setActiveWorkspace] = useState(workspaces[0] || '');
  const workspaceProfiles = profiles.filter(p => p.workspace === activeWorkspace);
  const runningProfiles = workspaceProfiles.filter(p => p.status === 'Running');
  const [activeProfileId, setActiveProfileId] = useState<string | null>(runningProfiles[0]?.id || workspaceProfiles[0]?.id || null);
  const activeProfile = workspaceProfiles.find(p => p.id === activeProfileId) || runningProfiles[0] || workspaceProfiles[0] || null;
  const [logs, setLogs] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

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
    if (!workspaceProfiles.length) {
      setActiveProfileId(null);
      return;
    }
    if (!activeProfileId || !workspaceProfiles.some(profile => profile.id === activeProfileId)) {
      setActiveProfileId((runningProfiles[0] || workspaceProfiles[0]).id);
    }
  }, [activeProfileId, runningProfiles, workspaceProfiles]);

  useEffect(() => {
    let cancelled = false;
    async function loadLogs() {
      if (!activeProfile) {
        if (!cancelled) {
          setLogs([]);
          setLogsLoading(false);
        }
        return;
      }
      if (!cancelled) {
        setLogsLoading(true);
      }
      const data = await fetchLogs(activeProfile.id, activeProfile.type);
      if (!cancelled) {
        setLogs(data);
        setLogsLoading(false);
      }
    }
    void loadLogs();
    const interval = window.setInterval(() => {
      void loadLogs();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeProfile]);

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 1.05, filter: 'blur(10px)' }}
      className="flex-1 flex flex-col max-w-6xl mx-auto w-full mt-8 mb-24 bg-[#0a0a0c] border border-white/10 rounded-[2rem] shadow-2xl overflow-hidden"
    >
      <div className="flex items-center justify-between gap-4 p-4 border-b border-white/5 bg-white/[0.02]">
        <div className="flex items-center gap-2 overflow-x-auto">
          {workspaces.map(ws => (
            <button
              key={ws}
              onClick={() => setActiveWorkspace(ws)}
              className={`px-4 py-2 rounded-xl text-sm font-medium font-mono transition-all whitespace-nowrap ${
                activeWorkspace === ws
                  ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 shadow-[0_0_15px_rgba(99,102,241,0.2)]'
                  : 'text-gray-500 hover:bg-white/5 hover:text-gray-300 border border-transparent'
              }`}
            >
              {ws}
            </button>
          ))}
        </div>
        <button
          onClick={onRefresh}
          className="p-3 rounded-xl bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors shrink-0"
          title="Refresh relay state"
        >
          <RefreshCw size={18} />
        </button>
      </div>

      <div className="px-6 py-4 border-b border-white/5 bg-black/20 flex flex-wrap gap-3 items-center min-h-[72px]">
        <span className="text-xs font-bold text-gray-600 uppercase tracking-widest mr-2">Workspace Relays</span>
        {workspaceProfiles.length === 0 ? (
          <span className="text-sm text-gray-500 italic">No relay profiles in this workspace.</span>
        ) : (
          workspaceProfiles.map(agent => (
            <button key={agent.id} onClick={() => setActiveProfileId(agent.id)} className="text-left">
              <GlowingNameplate agent={agent} />
            </button>
          ))
        )}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_320px] min-h-0 flex-1">
        <div className="min-h-0 border-r border-white/5">
          <div className="flex items-center justify-between px-6 py-4 border-b border-white/5 bg-white/[0.02]">
            <div>
              <div className="text-[10px] uppercase tracking-[0.22em] text-gray-500 font-bold">Live Relay Feed</div>
              <div className="mt-1 flex items-center gap-2">
                <span className="text-lg font-semibold text-white">{activeProfile?.name || 'No profile selected'}</span>
                {activeProfile ? (
                  <span className={`px-2 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider ${
                    activeProfile.type === 'Claude'
                      ? 'bg-orange-500/10 text-orange-400 border border-orange-500/20'
                      : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                  }`}>
                    {activeProfile.type}
                  </span>
                ) : null}
              </div>
              {activeProfile?.statusText ? (
                <div className="mt-2 text-sm text-gray-400 max-w-2xl">{activeProfile.statusText}</div>
              ) : null}
            </div>
            {activeProfile ? (
              <div className="text-right">
                <div className={`text-sm font-medium ${activeProfile.status === 'Running' ? 'text-white' : 'text-gray-500'}`}>
                  {activeProfile.status === 'Running' ? (activeProfile.state === 'working' ? 'Working' : 'Listening') : 'Stopped'}
                </div>
                <div className="text-xs text-gray-500 font-mono">{activeProfile.provider || 'runtime'}</div>
              </div>
            ) : null}
          </div>

          <div className="h-[520px] overflow-y-auto p-6 bg-black/30 font-mono text-xs text-gray-300 space-y-2">
            {!activeProfile ? (
              <div className="flex flex-col items-center justify-center h-full text-gray-500">
                <Activity size={36} className="mb-3 opacity-50" />
                <p>Select a relay to inspect its live feed.</p>
              </div>
            ) : logsLoading && logs.length === 0 ? (
              <div className="flex items-center gap-2 text-indigo-400">
                <Loader2 size={16} className="animate-spin" />
                Loading live feed...
              </div>
            ) : logs.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-gray-500">
                <FileText size={36} className="mb-3 opacity-50" />
                <p>No log lines yet.</p>
                <p className="text-gray-600 mt-2">Discord is still the command surface. This pane is operator visibility only.</p>
              </div>
            ) : (
              logs.map((line, i) => (
                <div key={`${activeProfile.id}-${i}-${line.slice(0, 32)}`} className="rounded-xl border border-white/5 bg-white/[0.02] px-4 py-3 leading-relaxed">
                  <span className="text-gray-500 mr-3">{String(i + 1).padStart(2, '0')}</span>
                  <span className={`${line.toLowerCase().includes('error') ? 'text-red-300' : line.toLowerCase().includes('working') ? 'text-indigo-300' : 'text-gray-200'}`}>
                    {line}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="p-6 bg-white/[0.02] border-t xl:border-t-0 border-white/5">
          <div className="text-[10px] uppercase tracking-[0.22em] text-gray-500 font-bold mb-4">Relay Inspector</div>
          {activeProfile ? (
            <div className="space-y-4">
              <InspectorRow label="Profile" value={activeProfile.name} />
              <InspectorRow label="Workspace" value={activeProfile.workspace} mono />
              <InspectorRow label="Worktree" value={activeProfile.activeWorktree || activeProfile.workspace} mono />
              <InspectorRow label="Status" value={`${activeProfile.status}${activeProfile.ready ? ' / ready' : ''}`} />
              <InspectorRow label="Relay Type" value={activeProfile.type} />
              <InspectorRow label="Bot Name" value={activeProfile.botName || activeProfile.name} />
              <InspectorRow label="Backend" value={activeProfile.provider || '-'} mono />
              <InspectorRow label="Model" value={activeProfile.model || (activeProfile.type === 'Claude' ? 'CLI default' : 'gpt-5.4')} mono />
              <InspectorRow label="Effort" value={activeProfile.effort || (activeProfile.type === 'Claude' ? 'adaptive prompt policy' : 'adaptive relay policy')} mono />
              <InspectorRow label="Trigger" value={activeProfile.triggerMode || '-'} mono />
              <InspectorRow label="DM Access" value={activeProfile.allowDms ? 'Enabled' : 'Disabled'} />
              <InspectorRow label="Namespace" value={activeProfile.stateNamespace || '-'} mono />
              <InspectorRow label="Channel" value={activeProfile.activeChannel || activeProfile.discordChannel || '-'} mono />
              <InspectorRow label="Session" value={activeProfile.sessionId || '-'} mono />
              <div className="pt-3 border-t border-white/5">
                <div className="text-[10px] uppercase tracking-[0.22em] text-gray-500 font-bold mb-2">Current Detail</div>
                <div className="rounded-2xl border border-white/5 bg-black/30 p-4 text-sm text-gray-300 leading-relaxed min-h-[96px]">
                  {activeProfile.statusText || 'No detailed relay status yet.'}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-gray-500">No relay selected.</div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

function GlowingNameplate({ agent }: { agent: Profile }) {
  const isClaude = agent.type === 'Claude';

  return (
    <div className={`relative flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all duration-500 ${
      agent.state === 'working'
        ? isClaude
          ? 'border-orange-400 shadow-[0_0_20px_rgba(249,115,22,0.6)] animate-pulse bg-orange-500/20 text-orange-300'
          : 'border-emerald-400 shadow-[0_0_20px_rgba(16,185,129,0.6)] animate-pulse bg-emerald-500/20 text-emerald-300'
        : 'border-gray-700 bg-white/5 text-gray-400'
    }`}>
      {isClaude ? <Bot size={14} /> : <Terminal size={14} />}
      <span className="font-bold text-sm tracking-wide">{agent.name}</span>

      {/* Activity Indicator */}
      <div className="absolute -top-1 -right-1 w-3 h-3">
        {agent.state === 'working' && (
          <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${isClaude ? 'bg-orange-400' : 'bg-emerald-400'} animate-ping`}></span>
        )}
        <span className={`relative inline-flex rounded-full h-3 w-3 border-2 border-[#0a0a0c] ${agent.state === 'idle' ? 'bg-gray-600' : isClaude ? 'bg-orange-500' : 'bg-emerald-500'}`}></span>
      </div>
    </div>
  );
}

function InspectorRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3 items-start">
      <div className="text-[10px] uppercase tracking-[0.22em] text-gray-500 font-bold pt-1">{label}</div>
      <div className={`rounded-xl border border-white/5 bg-black/30 px-3 py-2 text-sm text-gray-200 break-all ${mono ? 'font-mono' : ''}`}>
        {value}
      </div>
    </div>
  );
}

// --- Modals ---

function AddProfileModal({ onClose, onAdd }: {
  onClose: () => void;
  onAdd: (data: { name: string; type: ProfileType; workspace: string; discordToken: string; channelId: string; model?: string; triggerMode?: string; allowDms?: boolean; operatorIds?: string; allowedUserIds?: string }) => void;
}) {
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
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!name || !workspace || !discordToken || !channelId) return;
    setLoading(true);
    await onAdd({ name, type, workspace, discordToken, channelId, model, triggerMode, allowDms, operatorIds, allowedUserIds });
    setLoading(false);
  };

  return (
    <ModalWrapper onClose={onClose} title="Add Relay Profile">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <button
            onClick={() => setType('Claude')}
            className={`p-4 rounded-xl border-2 font-bold flex flex-col items-center gap-2 transition-colors ${
              type === 'Claude'
                ? 'border-orange-500/50 bg-orange-500/10 text-orange-400'
                : 'border-gray-700 bg-white/5 text-gray-400 hover:bg-white/10'
            }`}
          >
            <Bot size={24} /> Claude Code
          </button>
          <button
            onClick={() => setType('Codex')}
            className={`p-4 rounded-xl border-2 font-bold flex flex-col items-center gap-2 transition-colors ${
              type === 'Codex'
                ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-400'
                : 'border-gray-700 bg-white/5 text-gray-400 hover:bg-white/10'
            }`}
          >
            <Terminal size={24} /> Codex CLI
          </button>
        </div>
        <input
          type="text"
          placeholder="Profile display name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none"
        />
        <input
          type="text"
          placeholder="Workspace folder path"
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none font-mono text-sm"
        />
        <input
          type="password"
          placeholder="Discord bot token"
          value={discordToken}
          onChange={(e) => setDiscordToken(e.target.value)}
          className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none"
        />
        <input
          type="text"
          placeholder="Allowed Discord channel ID"
          value={channelId}
          onChange={(e) => setChannelId(e.target.value)}
          className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none"
        />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <input
            type="text"
            placeholder={type === 'Claude' ? 'Model override (optional)' : 'Codex model override (optional)'}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none font-mono text-sm"
          />
          <select
            value={triggerMode}
            onChange={(e) => setTriggerMode(e.target.value)}
            className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white outline-none"
          >
            <option value="mention_or_dm">Mention or DM</option>
            <option value="all">All messages</option>
            <option value="dm_only">DM only</option>
          </select>
        </div>
        <input
          type="text"
          placeholder="Operator IDs (comma-separated, optional)"
          value={operatorIds}
          onChange={(e) => setOperatorIds(e.target.value)}
          className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none font-mono text-sm"
        />
        <input
          type="text"
          placeholder="Additional allowed user IDs (comma-separated, optional)"
          value={allowedUserIds}
          onChange={(e) => setAllowedUserIds(e.target.value)}
          className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none font-mono text-sm"
        />
        <label className="flex items-center gap-3 rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-gray-300">
          <input type="checkbox" checked={allowDms} onChange={(e) => setAllowDms(e.target.checked)} className="h-4 w-4 accent-indigo-500" />
          Allow direct messages for approved users
        </label>
        <button
          onClick={handleSubmit}
          disabled={loading || !name || !workspace || !discordToken || !channelId}
          className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold shadow-[0_0_20px_rgba(99,102,241,0.4)] transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {loading ? <Loader2 size={18} className="animate-spin" /> : null}
          Save Profile
        </button>
      </div>
    </ModalWrapper>
  );
}

function SettingsModal({ onClose }: { onClose: () => void }) {
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchRuntimeInfo().then((payload) => {
      if (!cancelled) {
        setRuntimeInfo(payload);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ModalWrapper onClose={onClose} title="CLADEX Runtime">
      <div className="space-y-6">
        <p className="text-sm leading-relaxed text-gray-400">
          This panel shows the real desktop runtime state. Relay behavior is configured per profile, not through fake global controls.
        </p>
        <InspectorRow label="API" value={runtimeInfo?.apiBase || 'http://localhost:3001'} mono />
        <InspectorRow label="Backend" value={runtimeInfo?.backendDir || 'Loading...'} mono />
        <InspectorRow label="App Version" value={runtimeInfo?.appVersion || '2.0.0'} mono />
        <InspectorRow label="Packaging" value={runtimeInfo?.packaged ? 'Packaged desktop build' : 'Source/dev runtime'} />
        <div className="rounded-2xl border border-white/5 bg-black/30 p-4 text-sm text-gray-300 leading-relaxed">
          <div className="text-[10px] uppercase tracking-[0.22em] text-gray-500 font-bold mb-2">Parity Notes</div>
          <ul className="space-y-2 text-sm text-gray-400">
            <li>Codex uses app-server plus durable runtime state.</li>
            <li>Claude now uses the same durable runtime contract for worktrees, status, handoff, and task memory.</li>
            <li>Model and trigger behavior are set per profile during registration.</li>
          </ul>
        </div>
      </div>
    </ModalWrapper>
  );
}

function LogsModal({ profile, onClose }: { profile: Profile; onClose: () => void }) {
  const [logs, setLogs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const data = await fetchLogs(profile.id, profile.type);
      if (!cancelled) {
        setLogs(data);
        setLoading(false);
      }
    };
    void load();
    const interval = window.setInterval(() => {
      void load();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [profile]);

  return (
    <ModalWrapper onClose={onClose} title={`Live Logs · ${profile.name}`} wide>
      <div className="bg-black rounded-xl p-4 font-mono text-xs text-gray-400 h-64 overflow-y-auto space-y-1 border border-white/5 shadow-inner">
        {loading ? (
          <div className="flex items-center gap-2 text-indigo-400">
            <Loader2 size={14} className="animate-spin" /> Loading logs...
          </div>
        ) : logs.length === 0 ? (
          <div className="text-gray-500">No log lines recorded yet for this relay.</div>
        ) : (
          logs.map((line, i) => <div key={i}>{line}</div>)
        )}
        {profile.status === 'Running' && !loading && (
          <div className="flex items-center gap-2 text-indigo-400 mt-4">
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse"></span> Polling...
          </div>
        )}
      </div>
    </ModalWrapper>
  );
}

function ModalWrapper({ children, title, onClose, wide = false }: { children: React.ReactNode; title: string; onClose: () => void; wide?: boolean }) {
  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.9, y: 20 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.9, y: 20 }}
        onClick={(e) => e.stopPropagation()}
        className={`bg-[#0a0a0c] border border-white/10 rounded-3xl shadow-2xl overflow-hidden w-full ${wide ? 'max-w-2xl' : 'max-w-md'}`}
      >
        <div className="flex items-center justify-between p-6 border-b border-white/5 bg-white/[0.02]">
          <h3 className="font-bold text-lg text-white tracking-tight">{title}</h3>
          <button onClick={onClose} className="p-2 rounded-full bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>
        <div className="p-6">
          {children}
        </div>
      </motion.div>
    </motion.div>
  );
}

// --- Shared Components ---

function DockButton({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active?: boolean; onClick: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });

  const handleMouse = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (!ref.current) return;
    const { clientX, clientY } = e;
    const { height, width, left, top } = ref.current.getBoundingClientRect();
    const middleX = clientX - (left + width / 2);
    const middleY = clientY - (top + height / 2);
    setPosition({ x: middleX * 0.3, y: middleY * 0.3 });
  };

  const reset = () => {
    setPosition({ x: 0, y: 0 });
  };

  return (
    <div className="relative group">
      <motion.button
        ref={ref}
        onMouseMove={handleMouse}
        onMouseLeave={reset}
        animate={{ x: position.x, y: position.y }}
        transition={{ type: "spring", stiffness: 150, damping: 15, mass: 0.1 }}
        whileHover={{ scale: 1.1 }}
        whileTap={{ scale: 0.95 }}
        onClick={onClick}
        className={`p-3 rounded-xl transition-colors ${
          active
            ? 'bg-indigo-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.5)]'
            : 'text-gray-400 hover:bg-white/10 hover:text-white'
        }`}
      >
        {icon}
      </motion.button>
      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-3 px-3 py-1.5 bg-black/80 border border-white/10 text-white text-xs font-bold rounded-lg opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity whitespace-nowrap shadow-xl">
        {label}
      </div>
    </div>
  );
}
