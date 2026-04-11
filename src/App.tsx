import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence, useMotionValue, useTransform, useSpring, useMotionTemplate } from 'motion/react';
import {
  Terminal, Bot, Hash, Activity, Play, Square, Settings, X, 
  Send, Cpu, Wifi, WifiOff, Sparkles, Plus, FileText, Edit, Trash2, 
  MessageSquare, LayoutGrid, ChevronRight
} from 'lucide-react';

// --- Types & Mock Data ---

type ProfileType = 'Claude' | 'Codex';
type ProfileStatus = 'Running' | 'Stopped';
type AgentState = 'idle' | 'working';

interface Profile {
  id: string;
  name: string;
  type: ProfileType;
  workspace: string;
  status: ProfileStatus;
  discordChannel: string;
  state: AgentState;
}

const initialProfiles: Profile[] = [
  { id: '1', name: 'Claude-Architect', type: 'Claude', workspace: '~/dev/core', status: 'Running', discordChannel: 'core-arch', state: 'working' },
  { id: '2', name: 'Codex-API', type: 'Codex', workspace: '~/dev/api', status: 'Running', discordChannel: 'api-v2', state: 'working' },
  { id: '3', name: 'Claude-UI', type: 'Claude', workspace: '~/dev/ui', status: 'Stopped', discordChannel: 'frontend', state: 'idle' },
];

const mockMessages = [
  { id: 1, thread: '~/dev/api', author: 'User', isBot: false, text: 'Can we optimize the auth middleware?', time: '10:42 AM' },
  { id: 2, thread: '~/dev/api', author: 'Codex-API', isBot: true, type: 'Codex', text: 'Analyzing `auth.ts`... Found synchronous crypto calls. Rewriting to use async `crypto.subtle`.', time: '10:43 AM' },
  { id: 3, thread: '~/dev/core', author: 'User', isBot: false, text: 'Design the new database schema for users.', time: '11:00 AM' },
  { id: 4, thread: '~/dev/core', author: 'Claude-Architect', isBot: true, type: 'Claude', text: 'I am drafting the Prisma schema now. Thinking about the relation between Users and Workspaces...', time: '11:01 AM' },
];

// --- Main App Component ---

export default function App() {
  const [view, setView] = useState<'dashboard' | 'chat'>('dashboard');
  const [profiles, setProfiles] = useState<Profile[]>(initialProfiles);
  
  // Modals
  const [activeModal, setActiveModal] = useState<'add' | 'settings' | 'logs' | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);

  // Mouse tracking for global effects
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      setMousePos({ x: e.clientX, y: e.clientY });
    };
    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, []);

  const toggleStatus = (id: string) => {
    setProfiles(profiles.map(p => p.id === id ? { ...p, status: p.status === 'Running' ? 'Stopped' : 'Running', state: 'idle' } : p));
  };

  const deleteProfile = (id: string) => {
    setProfiles(profiles.filter(p => p.id !== id));
  };

  return (
    <div className="relative min-h-screen bg-[#050505] text-gray-100 font-sans overflow-hidden selection:bg-indigo-500/30">
      {/* Interactive Ambient Glow */}
      <motion.div 
        className="pointer-events-none fixed inset-0 z-0 opacity-40"
        animate={{
          background: `radial-gradient(circle 600px at ${mousePos.x}px ${mousePos.y}px, rgba(99, 102, 241, 0.15), transparent 80%)`
        }}
        transition={{ type: 'tween', ease: 'backOut', duration: 0.5 }}
      />
      
      <div className="absolute inset-0 bg-[url('https://grainy-gradients.vercel.app/noise.svg')] opacity-[0.15] mix-blend-overlay pointer-events-none z-0"></div>

      {/* Main Content Area */}
      <main className="relative z-10 h-screen flex flex-col pb-24">
        <AnimatePresence mode="wait">
          {view === 'dashboard' ? (
            <DashboardView 
              key="dashboard"
              profiles={profiles} 
              onToggle={toggleStatus} 
              onDelete={deleteProfile}
              onOpenLogs={(id) => { setSelectedProfileId(id); setActiveModal('logs'); }}
            />
          ) : (
            <ChatView 
              key="chat"
              profiles={profiles} 
            />
          )}
        </AnimatePresence>
      </main>

      {/* Floating Dock (Replaces Sidebar) */}
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
        <div className="flex items-center gap-2 p-2 rounded-2xl bg-white/5 border border-white/10 backdrop-blur-xl shadow-2xl shadow-black/50">
          <DockButton icon={<LayoutGrid />} label="Nexus" active={view === 'dashboard'} onClick={() => setView('dashboard')} />
          <DockButton icon={<MessageSquare />} label="Chatroom" active={view === 'chat'} onClick={() => setView('chat')} />
          <div className="w-px h-8 bg-white/10 mx-2" />
          <DockButton icon={<Plus />} label="Add Relay" onClick={() => setActiveModal('add')} />
          <DockButton icon={<Settings />} label="Settings" onClick={() => setActiveModal('settings')} />
        </div>
      </div>

      {/* Modals */}
      <AnimatePresence>
        {activeModal === 'add' && <AddProfileModal onClose={() => setActiveModal(null)} />}
        {activeModal === 'settings' && <SettingsModal onClose={() => setActiveModal(null)} />}
        {activeModal === 'logs' && <LogsModal profile={profiles.find(p => p.id === selectedProfileId)!} onClose={() => setActiveModal(null)} />}
      </AnimatePresence>
    </div>
  );
}

// --- Dashboard View (3D Cards) ---

function DashboardView({ profiles, onToggle, onDelete, onOpenLogs }: any) {
  return (
    <motion.div 
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20, filter: 'blur(10px)' }}
      className="flex-1 overflow-y-auto p-8 max-w-7xl mx-auto w-full"
    >
      <header className="mb-12 flex items-center gap-4">
        <div className="h-12 w-12 rounded-2xl bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center shadow-[0_0_30px_rgba(99,102,241,0.3)]">
          <Activity className="text-indigo-400" size={24} />
        </div>
        <div>
          <h1 className="text-3xl font-black tracking-tighter bg-clip-text text-transparent bg-gradient-to-r from-white to-gray-500 relative group cursor-default">
            RELAY NEXUS
            <span className="absolute inset-0 bg-clip-text text-transparent bg-gradient-to-r from-indigo-500 to-purple-500 opacity-0 group-hover:opacity-100 group-hover:animate-pulse transition-opacity duration-300 -translate-x-[1px] translate-y-[1px]">RELAY NEXUS</span>
            <span className="absolute inset-0 bg-clip-text text-transparent bg-gradient-to-r from-red-500 to-orange-500 opacity-0 group-hover:opacity-100 group-hover:animate-pulse transition-opacity duration-300 translate-x-[1px] -translate-y-[1px]" style={{ animationDelay: '50ms' }}>RELAY NEXUS</span>
          </h1>
          <p className="text-indigo-400 font-mono text-xs tracking-widest uppercase">System Overview</p>
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {profiles.map((profile: Profile, i: number) => (
          <InteractiveCard 
            key={profile.id} 
            profile={profile} 
            index={i}
            onToggle={() => onToggle(profile.id)}
            onDelete={() => onDelete(profile.id)}
            onOpenLogs={() => onOpenLogs(profile.id)}
          />
        ))}
      </div>
    </motion.div>
  );
}

function InteractiveCard({ profile, index, onToggle, onDelete, onOpenLogs }: any) {
  const isRunning = profile.status === 'Running';
  const isClaude = profile.type === 'Claude';
  const color = isClaude ? 'orange' : 'emerald';
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
            <button onClick={onDelete} className="p-2 rounded-full bg-white/5 hover:bg-red-500/20 text-gray-400 hover:text-red-400 transition-colors">
              <Trash2 size={14} />
            </button>
          </div>
        </div>

        {/* Middle: The Connection Visualization */}
        <div className="flex-1 flex items-center justify-center relative z-10 my-4" style={{ transform: "translateZ(40px)" }}>
          <div className="flex items-center w-full max-w-[200px] justify-between relative">
            {/* AI Node */}
            <div className={`relative z-10 h-10 w-10 rounded-xl flex items-center justify-center bg-[#0a0a0c] border-2 ${isRunning ? `border-${color}-500 shadow-[0_0_15px_${colorHex}80]` : 'border-gray-700'}`}>
              {isClaude ? <Bot size={18} className={isRunning ? `text-${color}-400` : 'text-gray-600'} /> : <Terminal size={18} className={isRunning ? `text-${color}-400` : 'text-gray-600'} />}
            </div>

            {/* Animated Line */}
            <div className="absolute left-10 right-10 h-[2px] bg-gray-800 overflow-hidden">
              {isRunning && (
                <motion.div 
                  className={`h-full w-1/2 bg-gradient-to-r from-transparent via-${color}-500 to-transparent`}
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

        {/* Bottom: Status & Big Toggle */}
        <div className="flex items-center justify-between relative z-10" style={{ transform: "translateZ(20px)" }}>
          <div className="flex flex-col">
            <span className="text-[10px] text-gray-500 uppercase tracking-wider font-bold">Status</span>
            <span className={`text-sm font-medium flex items-center gap-2 ${isRunning ? `text-${color}-400` : 'text-gray-500'}`}>
              {isRunning ? (
                <>
                  <span className={`w-2 h-2 rounded-full bg-${color}-500 animate-pulse`} />
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
            className={`flex items-center gap-2 px-4 py-2 rounded-xl font-bold text-sm transition-all ${
              isRunning 
                ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/30' 
                : `bg-${color}-500/10 text-${color}-400 hover:bg-${color}-500/20 border border-${color}-500/30`
            }`}
          >
            {isRunning ? <><Square size={14} fill="currentColor" /> Stop</> : <><Play size={14} fill="currentColor" /> Start</>}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

// --- Chatroom View (The "Sass") ---

function ChatView({ profiles }: { profiles: Profile[] }) {
  // Group threads by workspace
  const workspaces = Array.from(new Set(profiles.map(p => p.workspace)));
  const [activeWorkspace, setActiveWorkspace] = useState(workspaces[0]);

  const activeAgents = profiles.filter(p => p.workspace === activeWorkspace && p.status === 'Running');
  const threadMessages = mockMessages.filter(m => m.thread === activeWorkspace);

  return (
    <motion.div 
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 1.05, filter: 'blur(10px)' }}
      className="flex-1 flex flex-col max-w-5xl mx-auto w-full mt-8 mb-24 bg-[#0a0a0c] border border-white/10 rounded-[2rem] shadow-2xl overflow-hidden"
    >
      {/* Top Bar: Workspace Tabs */}
      <div className="flex items-center gap-2 p-4 border-b border-white/5 bg-white/[0.02]">
        {workspaces.map(ws => (
          <button
            key={ws}
            onClick={() => setActiveWorkspace(ws)}
            className={`px-4 py-2 rounded-xl text-sm font-medium font-mono transition-all ${
              activeWorkspace === ws 
                ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 shadow-[0_0_15px_rgba(99,102,241,0.2)]' 
                : 'text-gray-500 hover:bg-white/5 hover:text-gray-300 border border-transparent'
            }`}
          >
            {ws}
          </button>
        ))}
      </div>

      {/* Nameplates Area (The "Sass") */}
      <div className="px-6 py-4 border-b border-white/5 bg-black/20 flex flex-wrap gap-4 items-center min-h-[72px]">
        <span className="text-xs font-bold text-gray-600 uppercase tracking-widest mr-2">Active Relays:</span>
        {activeAgents.length === 0 ? (
          <span className="text-sm text-gray-500 italic">No agents running in this workspace.</span>
        ) : (
          activeAgents.map(agent => <GlowingNameplate key={agent.id} agent={agent} />)
        )}
      </div>

      {/* Chat Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {threadMessages.map((msg, i) => (
          <motion.div 
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.1 }}
            key={msg.id} 
            className={`flex gap-4 ${msg.isBot ? '' : 'flex-row-reverse'}`}
          >
            <div className={`w-10 h-10 rounded-2xl flex items-center justify-center shrink-0 border shadow-lg ${
              !msg.isBot ? 'bg-indigo-500/20 border-indigo-500/30 text-indigo-400' :
              msg.type === 'Claude' ? 'bg-orange-500/20 border-orange-500/30 text-orange-400' : 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400'
            }`}>
              {!msg.isBot ? 'U' : msg.type === 'Claude' ? <Bot size={20} /> : <Terminal size={20} />}
            </div>
            
            <div className={`flex flex-col max-w-[80%] ${!msg.isBot ? 'items-end' : 'items-start'}`}>
              <div className="flex items-baseline gap-2 mb-1">
                <span className="font-bold text-gray-300 text-sm">{msg.author}</span>
                <span className="text-xs text-gray-600">{msg.time}</span>
              </div>
              <div className={`px-5 py-3 rounded-2xl text-[15px] leading-relaxed ${
                !msg.isBot 
                  ? 'bg-indigo-600 text-white rounded-tr-sm shadow-[0_4px_20px_rgba(79,70,229,0.2)]' 
                  : 'bg-white/5 text-gray-200 rounded-tl-sm border border-white/10'
              }`}>
                {msg.text}
              </div>
            </div>
          </motion.div>
        ))}
      </div>

      {/* Input Area */}
      <div className="p-4 bg-white/[0.02] border-t border-white/5">
        <div className="relative flex items-center bg-black/50 border border-white/10 rounded-2xl p-2 focus-within:border-indigo-500/50 focus-within:shadow-[0_0_20px_rgba(99,102,241,0.2)] transition-all">
          <button className="p-3 text-gray-500 hover:text-indigo-400 transition-colors rounded-xl hover:bg-white/5">
            <Plus size={20} />
          </button>
          <input 
            type="text" 
            placeholder={`Send command to ${activeWorkspace}...`}
            className="flex-1 bg-transparent border-none focus:ring-0 text-gray-200 placeholder-gray-600 px-2"
          />
          <button className="p-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl transition-colors shadow-lg shadow-indigo-600/20">
            <Send size={18} />
          </button>
        </div>
      </div>
    </motion.div>
  );
}

function GlowingNameplate({ agent }: { agent: Profile }) {
  const isClaude = agent.type === 'Claude';
  const color = isClaude ? 'orange' : 'emerald';
  const colorHex = isClaude ? 'rgba(249, 115, 22, 0.6)' : 'rgba(16, 185, 129, 0.6)';

  let stateStyles = '';
  let dotStyles = '';

  if (agent.state === 'working') {
    stateStyles = `border-${color}-400 shadow-[0_0_20px_${colorHex}] animate-pulse bg-${color}-500/20 text-${color}-300`;
    dotStyles = `bg-${color}-400 animate-ping`;
  } else {
    stateStyles = `border-gray-700 bg-white/5 text-gray-400`;
    dotStyles = `bg-gray-600`;
  }

  return (
    <div className={`relative flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all duration-500 ${stateStyles}`}>
      {isClaude ? <Bot size={14} /> : <Terminal size={14} />}
      <span className="font-bold text-sm tracking-wide">{agent.name}</span>
      
      {/* Activity Indicator */}
      <div className="absolute -top-1 -right-1 w-3 h-3">
        {agent.state === 'working' && <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${dotStyles}`}></span>}
        <span className={`relative inline-flex rounded-full h-3 w-3 border-2 border-[#0a0a0c] ${agent.state === 'idle' ? 'bg-gray-600' : `bg-${color}-500`}`}></span>
      </div>
    </div>
  );
}

// --- Modals ---

function AddProfileModal({ onClose }: { onClose: () => void }) {
  return (
    <ModalWrapper onClose={onClose} title="Initialize New Relay">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <button className="p-4 rounded-xl border-2 border-orange-500/50 bg-orange-500/10 text-orange-400 font-bold flex flex-col items-center gap-2 hover:bg-orange-500/20 transition-colors">
            <Bot size={24} /> Claude Code
          </button>
          <button className="p-4 rounded-xl border-2 border-gray-700 bg-white/5 text-gray-400 font-bold flex flex-col items-center gap-2 hover:bg-white/10 transition-colors">
            <Terminal size={24} /> Codex CLI
          </button>
        </div>
        <input type="text" placeholder="Profile Name" className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none" />
        <input type="text" placeholder="Workspace Path (e.g. ~/dev/project)" className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none font-mono text-sm" />
        <input type="text" placeholder="Discord Channel ID" className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white focus:border-indigo-500 outline-none" />
        <button onClick={onClose} className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold shadow-[0_0_20px_rgba(99,102,241,0.4)] transition-all">
          Deploy Relay
        </button>
      </div>
    </ModalWrapper>
  );
}

function SettingsModal({ onClose }: { onClose: () => void }) {
  return (
    <ModalWrapper onClose={onClose} title="System Settings">
      <div className="space-y-6">
        <div>
          <label className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-2 block">Log Retention</label>
          <select className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white outline-none appearance-none">
            <option>7 Days</option>
            <option>14 Days</option>
            <option>30 Days</option>
          </select>
        </div>
        <div>
          <label className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-2 block">Default Port Range</label>
          <input type="number" defaultValue={8080} className="w-full bg-black/50 border border-white/10 rounded-xl p-3 text-white outline-none font-mono" />
        </div>
      </div>
    </ModalWrapper>
  );
}

function LogsModal({ profile, onClose }: { profile: Profile, onClose: () => void }) {
  return (
    <ModalWrapper onClose={onClose} title={`Terminal: ${profile.name}`} wide>
      <div className="bg-black rounded-xl p-4 font-mono text-xs text-gray-400 h-64 overflow-y-auto space-y-2 border border-white/5 shadow-inner">
        <div>{'>'} [SYSTEM] Initializing relay daemon for {profile.workspace}...</div>
        <div>{'>'} [DISCORD] Authenticated as bot. Listening on #{profile.discordChannel}.</div>
        <div className="text-emerald-400">{'>'} [READY] Awaiting commands.</div>
        {profile.status === 'Running' && (
          <div className="flex items-center gap-2 text-indigo-400 mt-4">
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse"></span> Polling...
          </div>
        )}
      </div>
    </ModalWrapper>
  );
}

function ModalWrapper({ children, title, onClose, wide = false }: { children: React.ReactNode, title: string, onClose: () => void, wide?: boolean }) {
  return (
    <motion.div 
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
    >
      <motion.div 
        initial={{ scale: 0.9, y: 20 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.9, y: 20 }}
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

function DockButton({ icon, label, active, onClick }: { icon: React.ReactNode, label: string, active?: boolean, onClick: () => void }) {
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


