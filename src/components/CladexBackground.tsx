import { useEffect, useRef } from 'react';
import { motion } from 'motion/react';

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  color: string;
};

const COLORS = ['#d4735e', '#7db5a5', '#6366f1', '#64748b'];

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
    const mouse = { x: -1000, y: -1000 };

    const particleCount = () => Math.max(36, Math.min(92, Math.floor(window.innerWidth / 22)));

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      particles = Array.from({ length: particleCount() }, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx: (Math.random() - 0.5) * 0.45,
        vy: (Math.random() - 0.5) * 0.45,
        size: Math.random() * 1.8 + 0.8,
        color: COLORS[Math.floor(Math.random() * COLORS.length)],
      }));
    };

    const onMouseMove = (event: MouseEvent) => {
      mouse.x = event.clientX;
      mouse.y = event.clientY;
    };

    const onMouseLeave = () => {
      mouse.x = -1000;
      mouse.y = -1000;
    };

    const animate = () => {
      frame += 0.004;
      ctx.fillStyle = isDark ? '#050505' : '#f2efe7';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      for (const particle of particles) {
        const dx = particle.x - mouse.x;
        const dy = particle.y - mouse.y;
        const distanceSq = dx * dx + dy * dy;
        if (distanceSq < 220 * 220) {
          const distance = Math.max(1, Math.sqrt(distanceSq));
          const push = (220 - distance) / 220;
          particle.vx += (dx / distance) * push * 0.08;
          particle.vy += (dy / distance) * push * 0.08;
        }

        particle.vx *= 0.992;
        particle.vy *= 0.992;
        particle.x += particle.vx + Math.sin(frame + particle.y * 0.005) * 0.08;
        particle.y += particle.vy + Math.cos(frame + particle.x * 0.004) * 0.08;

        if (particle.x < -40) particle.x = canvas.width + 40;
        if (particle.x > canvas.width + 40) particle.x = -40;
        if (particle.y < -40) particle.y = canvas.height + 40;
        if (particle.y > canvas.height + 40) particle.y = -40;

        ctx.beginPath();
        ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
        ctx.fillStyle = particle.color;
        ctx.globalAlpha = 0.28;
        ctx.fill();
      }
      ctx.globalAlpha = 1;
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
        className={`absolute -left-[12%] top-[-18%] h-[48rem] w-[48rem] rounded-full blur-[130px] transition-colors duration-500 ${isDark ? 'bg-[#d4735e]/12' : 'bg-[#d4735e]/20 mix-blend-multiply'}`}
        animate={{ x: [0, 36, 0], y: [0, 18, 0], scale: [1, 1.04, 1] }}
        transition={{ duration: 24, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        className={`absolute bottom-[-22%] right-[-8%] h-[50rem] w-[50rem] rounded-full blur-[160px] transition-colors duration-500 ${isDark ? 'bg-[#7db5a5]/12' : 'bg-[#7db5a5]/18 mix-blend-multiply'}`}
        animate={{ x: [0, -44, 0], y: [0, -28, 0], scale: [1, 1.08, 1] }}
        transition={{ duration: 28, repeat: Infinity, ease: 'easeInOut', delay: 1.5 }}
      />
      <div className={`absolute inset-0 ${isDark ? 'bg-[radial-gradient(circle_at_center,transparent_0,rgba(5,5,5,0.1)_55%,rgba(5,5,5,0.45)_100%)]' : 'bg-[radial-gradient(circle_at_center,transparent_0,rgba(242,239,231,0.16)_55%,rgba(242,239,231,0.76)_100%)]'}`} />
      <div
        className={`absolute inset-0 opacity-[0.08] ${isDark ? 'mix-blend-overlay' : 'mix-blend-multiply'}`}
        style={{
          backgroundImage:
            'radial-gradient(circle at 20% 20%, rgba(255,255,255,0.7) 0, transparent 22%), radial-gradient(circle at 80% 30%, rgba(255,255,255,0.65) 0, transparent 20%), radial-gradient(circle at 50% 80%, rgba(255,255,255,0.55) 0, transparent 18%)',
        }}
      />
    </div>
  );
}
