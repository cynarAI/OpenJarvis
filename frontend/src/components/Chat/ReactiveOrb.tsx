import { motion } from 'motion/react';

export type OrbState = 'idle' | 'thinking';

interface ReactiveOrbProps {
  state?: OrbState;
  size?: number;
}

/**
 * JARVIS-style reactive core.
 *
 * Pure CSS/SVG + motion — no WebGL — so it's cheap to render and safe to
 * ship everywhere the greeting screen shows up (including low-power
 * Tauri desktop builds). Reuses the existing HUD design tokens
 * (--color-accent, --color-accent-purple) so it tracks light/dark theme
 * and the OpenJarvis palette automatically.
 */
export function ReactiveOrb({ state = 'idle', size = 112 }: ReactiveOrbProps) {
  const isThinking = state === 'thinking';

  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {/* Outer dashed reticle ring — slow constant rotation, speeds up when thinking */}
      <motion.div
        className="absolute inset-0 rounded-full"
        style={{ border: '1px dashed color-mix(in srgb, var(--color-accent) 55%, transparent)' }}
        animate={{ rotate: 360 }}
        transition={{ repeat: Infinity, ease: 'linear', duration: isThinking ? 5 : 14 }}
      />

      {/* Middle ring — counter-rotates, tick marks via conic gradient */}
      <motion.div
        className="absolute rounded-full"
        style={{
          inset: size * 0.1,
          background:
            'conic-gradient(from 0deg, color-mix(in srgb, var(--color-accent) 60%, transparent) 0deg, transparent 12deg, transparent 78deg, color-mix(in srgb, var(--color-accent) 60%, transparent) 90deg, transparent 102deg, transparent 168deg, color-mix(in srgb, var(--color-accent) 60%, transparent) 180deg, transparent 192deg, transparent 258deg, color-mix(in srgb, var(--color-accent) 60%, transparent) 270deg, transparent 282deg, transparent 348deg)',
          WebkitMaskImage: 'radial-gradient(closest-side, transparent calc(100% - 2px), black calc(100% - 1.5px))',
          maskImage: 'radial-gradient(closest-side, transparent calc(100% - 2px), black calc(100% - 1.5px))',
          opacity: 0.8,
        }}
        animate={{ rotate: -360 }}
        transition={{ repeat: Infinity, ease: 'linear', duration: isThinking ? 8 : 22 }}
      />

      {/* Orbiting satellite dots — only visible while thinking */}
      {isThinking &&
        [0, 120, 240].map((offset) => (
          <motion.div
            key={offset}
            className="absolute rounded-full"
            style={{
              inset: size * 0.02,
              transformOrigin: 'center',
            }}
            animate={{ rotate: offset + 360 }}
            transition={{ repeat: Infinity, ease: 'linear', duration: 2.4 }}
          >
            <span
              className="absolute rounded-full"
              style={{
                top: 0,
                left: '50%',
                width: 4,
                height: 4,
                background: 'var(--color-accent)',
                boxShadow: '0 0 6px 1px var(--color-accent-glow)',
                transform: 'translateX(-50%)',
              }}
            />
          </motion.div>
        ))}

      {/* Breathing glow core */}
      <motion.div
        className="absolute rounded-full"
        style={{
          inset: size * 0.28,
          background:
            'radial-gradient(circle at 35% 30%, color-mix(in srgb, var(--color-accent) 85%, white), var(--color-accent) 55%, color-mix(in srgb, var(--color-accent) 40%, transparent) 100%)',
        }}
        animate={{
          scale: isThinking ? [1, 1.12, 1] : [1, 1.04, 1],
          boxShadow: isThinking
            ? [
                '0 0 16px 2px var(--color-accent-glow)',
                '0 0 34px 10px var(--color-accent-glow)',
                '0 0 16px 2px var(--color-accent-glow)',
              ]
            : [
                '0 0 10px 1px var(--color-accent-glow)',
                '0 0 18px 4px var(--color-accent-glow)',
                '0 0 10px 1px var(--color-accent-glow)',
              ],
        }}
        transition={{
          repeat: Infinity,
          ease: 'easeInOut',
          duration: isThinking ? 1.3 : 2.8,
        }}
      />

      {/* Core specular highlight */}
      <div
        className="absolute rounded-full pointer-events-none"
        style={{
          inset: size * 0.28,
          background: 'radial-gradient(circle at 32% 26%, rgba(255,255,255,0.55), transparent 42%)',
        }}
      />
    </div>
  );
}
