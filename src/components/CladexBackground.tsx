import { useEffect, useRef } from 'react';
import { motion } from 'motion/react';

type Particle = {
  x: number;
  y: number;
  baseVx: number;
  baseVy: number;
  vx: number;
  vy: number;
  size: number;
  color: string;
  glow: number;
};

const PALETTE = ['#d4735e', '#7db5a5', '#5865f2', '#64748b'];

export default function CladexBackground({ isDark }: { isDark: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) {
      return;
    }

    let frame = 0;
    let animationFrameId = 0;
    let particles: Particle[] = [];
    const mouse = { x: -1000, y: -1000, vx: 0, vy: 0 };
    const lastMouse = { x: -1000, y: -1000 };

    const particleCount = () => Math.min(84, Math.max(42, Math.floor(window.innerWidth / 22)));

    const initParticles = () => {
      particles = Array.from({ length: particleCount() }, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        baseVx: (Math.random() - 0.5) * 0.45,
        baseVy: (Math.random() - 0.5) * 0.45,
        vx: 0,
        vy: 0,
        size: Math.random() * 1.9 + 0.9,
        color: PALETTE[Math.floor(Math.random() * PALETTE.length)],
        glow: 0,
      }));
    };

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initParticles();
    };

    const onMouseMove = (event: MouseEvent) => {
      lastMouse.x = mouse.x;
      lastMouse.y = mouse.y;
      mouse.x = event.clientX;
      mouse.y = event.clientY;

      if (lastMouse.x !== -1000) {
        mouse.vx = mouse.x - lastMouse.x;
        mouse.vy = mouse.y - lastMouse.y;
      }
    };

    const onMouseLeave = () => {
      mouse.x = -1000;
      mouse.y = -1000;
      mouse.vx = 0;
      mouse.vy = 0;
    };

    const animate = () => {
      frame += 0.004;
      ctx.fillStyle = isDark ? '#050505' : '#f2efe7';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      mouse.vx *= 0.92;
      mouse.vy *= 0.92;

      for (const particle of particles) {
        const dx = particle.x - mouse.x;
        const dy = particle.y - mouse.y;
        const distanceSq = dx * dx + dy * dy;
        const interactionRadius = 220;

        if (distanceSq < interactionRadius * interactionRadius) {
          const distance = Math.max(1, Math.sqrt(distanceSq));
          const force = (interactionRadius - distance) / interactionRadius;
          particle.vx += (dx / distance) * force * 0.16;
          particle.vy += (dy / distance) * force * 0.16;
          particle.vx += mouse.vx * force * 0.015;
          particle.vy += mouse.vy * force * 0.015;
          particle.glow = force;
        } else {
          particle.glow *= 0.93;
        }

        particle.vx *= 0.985;
        particle.vy *= 0.985;
        particle.x += particle.baseVx + particle.vx + Math.sin(frame + particle.y * 0.004) * 0.04;
        particle.y += particle.baseVy + particle.vy + Math.cos(frame + particle.x * 0.003) * 0.04;

        if (particle.x < -40) particle.x = canvas.width + 40;
        if (particle.x > canvas.width + 40) particle.x = -40;
        if (particle.y < -40) particle.y = canvas.height + 40;
        if (particle.y > canvas.height + 40) particle.y = -40;

        ctx.beginPath();
        ctx.arc(particle.x, particle.y, particle.size + particle.glow * 1.6, 0, Math.PI * 2);
        ctx.shadowBlur = 16 * particle.glow;
        ctx.shadowColor = particle.color;
        ctx.globalAlpha = Math.min(0.55, 0.18 + particle.glow * 0.42);
        ctx.fillStyle = particle.color;
        ctx.fill();
      }

      ctx.globalAlpha = 1;
      ctx.shadowBlur = 0;
      animationFrameId = window.requestAnimationFrame(animate);
    };

    resize();
    animate();
    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseleave', onMouseLeave);

    return () => {
      window.cancelAnimationFrame(animationFrameId);
      window.removeEventListener('resize', resize);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseleave', onMouseLeave);
    };
  }, [isDark]);

  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      <motion.div
        className={`absolute -left-[12%] top-[-18%] h-[52rem] w-[52rem] rounded-full blur-[140px] transition-colors duration-500 ${isDark ? 'bg-[#d4735e]/12 mix-blend-screen' : 'bg-[#d4735e]/20 mix-blend-multiply'}`}
        animate={{ x: [0, 42, 0], y: [0, 26, 0], scale: [1, 1.04, 1] }}
        transition={{ duration: 22, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        className={`absolute bottom-[-22%] right-[-10%] h-[54rem] w-[54rem] rounded-full blur-[160px] transition-colors duration-500 ${isDark ? 'bg-[#7db5a5]/12 mix-blend-screen' : 'bg-[#7db5a5]/18 mix-blend-multiply'}`}
        animate={{ x: [0, -48, 0], y: [0, -22, 0], scale: [1, 1.08, 1] }}
        transition={{ duration: 26, repeat: Infinity, ease: 'easeInOut', delay: 1.3 }}
      />
      <div className={`absolute inset-0 ${isDark ? 'bg-[radial-gradient(circle_at_center,transparent_0,rgba(5,5,5,0.08)_52%,rgba(5,5,5,0.5)_100%)]' : 'bg-[radial-gradient(circle_at_center,transparent_0,rgba(242,239,231,0.12)_50%,rgba(242,239,231,0.72)_100%)]'}`} />
      <div
        className={`absolute inset-0 opacity-[0.08] ${isDark ? 'mix-blend-overlay' : 'mix-blend-multiply'}`}
        style={{
          backgroundImage:
            'radial-gradient(circle at 20% 20%, rgba(255,255,255,0.75) 0, transparent 22%), radial-gradient(circle at 80% 30%, rgba(255,255,255,0.65) 0, transparent 20%), radial-gradient(circle at 50% 80%, rgba(255,255,255,0.55) 0, transparent 18%)',
        }}
      />
      <div
        className="absolute inset-0 opacity-[0.05]"
        style={{
          backgroundImage:
            'linear-gradient(to right, rgba(255,255,255,0.08) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.08) 1px, transparent 1px)',
          backgroundSize: '32px 32px',
        }}
      />
    </div>
  );
}
