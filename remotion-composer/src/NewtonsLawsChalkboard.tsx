import React from "react";
import {
  AbsoluteFill,
  Audio,
  Easing,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const fontFamily = "Inter, 'Space Grotesk', -apple-system, BlinkMacSystemFont, sans-serif";

// Chalkboard Color Tokens
const CHALK = {
  bg: "#0D131A",
  boardSurface: "#131C26",
  grid: "rgba(255, 255, 255, 0.035)",
  border: "#253446",
  white: "#F8FAFC",
  muted: "#94A3B8",
  cyan: "#38BDF8",
  gold: "#FBBF24",
  coral: "#F87171",
  green: "#34D399",
  purple: "#A78BFA",
};

// Reusable Chalk Frame wrapper with subtle dust particles and grid
const ChalkboardCanvas: React.FC<{ children: React.ReactNode; sceneTag?: string }> = ({
  children,
  sceneTag,
}) => {
  const frame = useCurrentFrame();
  const pulse = Math.sin(frame / 20) * 0.02;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: CHALK.bg,
        backgroundImage: `
          linear-gradient(${CHALK.grid} 2px, transparent 2px),
          linear-gradient(90deg, ${CHALK.grid} 2px, transparent 2px)
        `,
        backgroundSize: "60px 60px",
        fontFamily,
        color: CHALK.white,
        overflow: "hidden",
      }}
    >
      {/* Outer Chalkboard Wooden/Slate Bezel */}
      <div
        style={{
          position: "absolute",
          inset: 24,
          border: `4px solid ${CHALK.border}`,
          borderRadius: 28,
          pointerEvents: "none",
          boxShadow: "inset 0 0 60px rgba(0,0,0,0.8)",
        }}
      />

      {/* Chalkboard Header Bar */}
      <div
        style={{
          position: "absolute",
          top: 50,
          left: 60,
          right: 60,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: `2px dashed ${CHALK.border}`,
          paddingBottom: 20,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div
            style={{
              width: 14,
              height: 14,
              borderRadius: "50%",
              backgroundColor: CHALK.cyan,
              boxShadow: `0 0 12px ${CHALK.cyan}`,
            }}
          />
          <span
            style={{
              fontSize: 26,
              fontWeight: 800,
              letterSpacing: 2,
              color: CHALK.cyan,
              textTransform: "uppercase",
            }}
          >
            PHYSICS MASTERCLASS
          </span>
        </div>
        {sceneTag && (
          <div
            style={{
              padding: "6px 16px",
              borderRadius: 20,
              backgroundColor: "rgba(56, 189, 248, 0.12)",
              border: `1px solid ${CHALK.cyan}`,
              fontSize: 20,
              fontWeight: 700,
              color: CHALK.gold,
              letterSpacing: 1,
            }}
          >
            {sceneTag}
          </div>
        )}
      </div>

      {/* Main Content Area */}
      <div
        style={{
          position: "absolute",
          top: 130,
          bottom: 240,
          left: 60,
          right: 60,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        {children}
      </div>

      {/* Bottom Chalk Dust Bar */}
      <div
        style={{
          position: "absolute",
          bottom: 40,
          left: 60,
          right: 60,
          height: 8,
          borderRadius: 4,
          background: `linear-gradient(90deg, ${CHALK.cyan}22, ${CHALK.gold}44, ${CHALK.cyan}22)`,
        }}
      />
    </AbsoluteFill>
  );
};

// Animated Subtitle Card at bottom of screen
const SubtitleCard: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 15 } });

  return (
    <div
      style={{
        position: "absolute",
        bottom: 70,
        left: 50,
        right: 50,
        backgroundColor: "rgba(19, 28, 38, 0.94)",
        border: `2px solid ${CHALK.border}`,
        borderRadius: 24,
        padding: "24px 32px",
        boxShadow: "0 20px 40px rgba(0,0,0,0.6)",
        transform: `translateY(${(1 - enter) * 30}px)`,
        opacity: enter,
      }}
    >
      <div
        style={{
          fontSize: 32,
          fontWeight: 600,
          lineHeight: 1.4,
          color: CHALK.white,
          textAlign: "center",
        }}
      >
        "{text}"
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// SCENE 1: Hook & Friction Trap (0.0s - 12.26s = 368 frames)
// ---------------------------------------------------------------------------
const Scene1Hook: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Book slides right and decelerates
  const slideProgress = interpolate(frame, [15, 120], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  const bookX = interpolate(slideProgress, [0, 1], [-220, 180]);
  const frictionAppear = spring({ frame: frame - 60, fps, config: { damping: 14 } });

  return (
    <ChalkboardCanvas sceneTag="PART 1 · THE HOOK">
      <div style={{ textAlign: "center", marginBottom: 40 }}>
        <h1 style={{ fontSize: 58, fontWeight: 900, color: CHALK.white, margin: "0 0 16px" }}>
          NEWTON'S LAWS
        </h1>
        <h2 style={{ fontSize: 34, fontWeight: 700, color: CHALK.gold, margin: 0 }}>
          Why Motion Feels Backwards
        </h2>
      </div>

      {/* SVG Diagram: Sliding Book on Table */}
      <svg width="860" height="420" viewBox="0 0 860 420" style={{ overflow: "visible" }}>
        {/* Table Surface */}
        <line x1="80" y1="300" x2="780" y2="300" stroke={CHALK.white} strokeWidth="6" strokeLinecap="round" />
        {/* Surface texture dashes */}
        {[140, 240, 340, 440, 540, 640, 740].map((x) => (
          <line key={x} x1={x} y1="305" x2={x - 20} y2="330" stroke={CHALK.muted} strokeWidth="3" />
        ))}

        {/* Sliding Book */}
        <g transform={`translate(${430 + bookX}, 200)`}>
          <rect
            x="-90"
            y="-70"
            width="180"
            height="100"
            rx="12"
            fill="rgba(56, 189, 248, 0.2)"
            stroke={CHALK.cyan}
            strokeWidth="5"
          />
          <text x="0" y="-12" fill={CHALK.white} fontSize="26" fontWeight="800" textAnchor="middle">
            BOOK
          </text>
          <text x="0" y="16" fill={CHALK.cyan} fontSize="18" fontWeight="600" textAnchor="middle">
            (Sliding Right)
          </text>

          {/* Velocity Vector Arrow */}
          <line x1="100" y1="-20" x2="190" y2="-20" stroke={CHALK.cyan} strokeWidth="6" strokeLinecap="round" />
          <polygon points="190,-28 206,-20 190,-12" fill={CHALK.cyan} />
          <text x="145" y="-36" fill={CHALK.cyan} fontSize="20" fontWeight="700" textAnchor="middle">
            v →
          </text>

          {/* Opposing Friction Arrow */}
          {frictionAppear > 0.05 && (
            <g opacity={frictionAppear} transform={`scale(${frictionAppear})`} transformOrigin="0 0">
              <line x1="-100" y1="-20" x2="-220" y2="-20" stroke={CHALK.coral} strokeWidth="7" strokeLinecap="round" />
              <polygon points="-220,-28 -238,-20 -220,-12" fill={CHALK.coral} />
              <text x="-160" y="-38" fill={CHALK.coral} fontSize="22" fontWeight="800" textAnchor="middle">
                ← FRICTION
              </text>
            </g>
          )}
        </g>
      </svg>

      {/* Misconception Alert Callout */}
      <div
        style={{
          marginTop: 20,
          backgroundColor: "rgba(248, 113, 113, 0.12)",
          border: `2px dashed ${CHALK.coral}`,
          borderRadius: 20,
          padding: "18px 30px",
          textAlign: "center",
          maxWidth: 720,
        }}
      >
        <span style={{ fontSize: 24, fontWeight: 800, color: CHALK.coral }}>
          COMMON TRAP: "Motion needs a continuous push to continue."
        </span>
        <br />
        <span style={{ fontSize: 22, fontWeight: 600, color: CHALK.white }}>
          NO! Friction is an outside force actively stopping it.
        </span>
      </div>

      <SubtitleCard text="Why does motion feel backwards? Slide a book across a table, and it stops. But not because motion needs a push. It stops because friction is fighting it!" />
      <Audio src={staticFile("audio/newtons-laws/sec-1.mp3")} />
    </ChalkboardCanvas>
  );
};

// ---------------------------------------------------------------------------
// SCENE 2: Newton's First Law — Inertia (12.26s - 26.47s = 426 frames)
// ---------------------------------------------------------------------------
const Scene2Inertia: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Space probe drift
  const drift = interpolate(frame, [0, 420], [-180, 240]);
  const b1 = spring({ frame: frame - 30, fps, config: { damping: 14 } });
  const b2 = spring({ frame: frame - 90, fps, config: { damping: 14 } });
  const b3 = spring({ frame: frame - 150, fps, config: { damping: 14 } });

  return (
    <ChalkboardCanvas sceneTag="LAW 1 · INERTIA">
      <div style={{ textAlign: "center", marginBottom: 30 }}>
        <h2 style={{ fontSize: 52, fontWeight: 900, color: CHALK.gold, margin: "0 0 12px" }}>
          LAW 1: THE LAW OF INERTIA
        </h2>
        <div
          style={{
            display: "inline-block",
            padding: "8px 24px",
            borderRadius: 16,
            backgroundColor: "rgba(251, 191, 36, 0.15)",
            border: `2px solid ${CHALK.gold}`,
            fontSize: 30,
            fontWeight: 800,
            color: CHALK.white,
          }}
        >
          ΣF = 0 ⟹ Velocity is Constant
        </div>
      </div>

      {/* SVG Deep Space Probe Diagram */}
      <svg width="860" height="340" viewBox="0 0 860 340">
        {/* Twinkling Chalk Stars */}
        {[
          [100, 60], [240, 180], [380, 50], [540, 120], [700, 80],
          [160, 280], [320, 290], [620, 260], [780, 220],
        ].map(([sx, sy], i) => (
          <circle key={i} cx={sx} cy={sy} r={(i % 3) + 2} fill={CHALK.white} opacity={0.4 + Math.sin((frame + i * 20) / 10) * 0.3} />
        ))}

        {/* Space Probe Gliding Forever */}
        <g transform={`translate(${430 + drift}, 170)`}>
          {/* Solar panels */}
          <rect x="-90" y="-12" width="60" height="24" rx="4" fill="rgba(56, 189, 248, 0.4)" stroke={CHALK.cyan} strokeWidth="3" />
          <rect x="30" y="-12" width="60" height="24" rx="4" fill="rgba(56, 189, 248, 0.4)" stroke={CHALK.cyan} strokeWidth="3" />
          {/* Main Body */}
          <circle cx="0" cy="0" r="30" fill={CHALK.gold} stroke={CHALK.white} strokeWidth="4" />
          {/* Dish Antenna */}
          <path d="M-10 -30 Q0 -48 20 -35" stroke={CHALK.white} strokeWidth="4" fill="none" />
          {/* Infinite Motion Arrow */}
          <line x1="40" y1="0" x2="160" y2="0" stroke={CHALK.green} strokeWidth="6" strokeLinecap="round" strokeDasharray="12 6" />
          <polygon points="160,-8 178,0 160,8" fill={CHALK.green} />
          <text x="100" y="32" fill={CHALK.green} fontSize="20" fontWeight="800" textAnchor="middle">
            GLIDES FOREVER →
          </text>
        </g>
      </svg>

      {/* Core Rules List */}
      <div style={{ width: "100%", maxWidth: 740, display: "flex", flexDirection: "column", gap: 14 }}>
        <div
          style={{
            opacity: b1,
            transform: `translateX(${(1 - b1) * -40}px)`,
            backgroundColor: CHALK.boardSurface,
            borderLeft: `6px solid ${CHALK.cyan}`,
            padding: "14px 20px",
            borderRadius: "0 14px 14px 0",
            fontSize: 24,
            fontWeight: 700,
          }}
        >
          ✦ An object in motion stays in motion at constant velocity.
        </div>
        <div
          style={{
            opacity: b2,
            transform: `translateX(${(1 - b2) * -40}px)`,
            backgroundColor: CHALK.boardSurface,
            borderLeft: `6px solid ${CHALK.gold}`,
            padding: "14px 20px",
            borderRadius: "0 14px 14px 0",
            fontSize: 24,
            fontWeight: 700,
          }}
        >
          ✦ An object at rest stays at rest.
        </div>
        <div
          style={{
            opacity: b3,
            transform: `translateX(${(1 - b3) * -40}px)`,
            backgroundColor: CHALK.boardSurface,
            borderLeft: `6px solid ${CHALK.green}`,
            padding: "14px 20px",
            borderRadius: "0 14px 14px 0",
            fontSize: 24,
            fontWeight: 700,
          }}
        >
          ✦ UNLESS acted upon by an unbalanced outside force.
        </div>
      </div>

      <SubtitleCard text="That is Newton's First Law: Inertia! An object in motion stays in motion unless an unbalanced force acts on it. In deep space, with zero friction, you glide forever without burning any fuel." />
      <Audio src={staticFile("audio/newtons-laws/sec-2.mp3")} />
    </ChalkboardCanvas>
  );
};

// ---------------------------------------------------------------------------
// SCENE 3: Newton's Second Law — F = ma (26.47s - 43.61s = 514 frames)
// ---------------------------------------------------------------------------
const Scene3SecondLaw: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Soccer ball accelerates fast
  const ballSpeed = interpolate(frame, [30, 200], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const ballOffset = ballSpeed * 220;

  // Boulder barely moves
  const boulderSpeed = interpolate(frame, [30, 200], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const boulderOffset = boulderSpeed * 15;

  return (
    <ChalkboardCanvas sceneTag="LAW 2 · F = ma">
      <div style={{ textAlign: "center", marginBottom: 20 }}>
        <h2 style={{ fontSize: 50, fontWeight: 900, color: CHALK.cyan, margin: "0 0 10px" }}>
          LAW 2: FORCE = MASS × ACCELERATION
        </h2>
        <div
          style={{
            display: "inline-block",
            padding: "8px 28px",
            borderRadius: 16,
            backgroundColor: "rgba(56, 189, 248, 0.15)",
            border: `2px solid ${CHALK.cyan}`,
            fontSize: 34,
            fontWeight: 900,
            color: CHALK.gold,
            letterSpacing: 2,
          }}
        >
          a = F / m
        </div>
      </div>

      {/* Comparative Diagram: Soccer Ball vs Boulder */}
      <div style={{ display: "flex", gap: 30, width: "100%", maxWidth: 840 }}>
        {/* Left Card: Light Soccer Ball */}
        <div
          style={{
            flex: 1,
            backgroundColor: CHALK.boardSurface,
            borderRadius: 20,
            border: `2px solid ${CHALK.green}`,
            padding: "20px 16px",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 24, fontWeight: 800, color: CHALK.green, marginBottom: 12 }}>
            LIGHT SOCCER BALL
          </div>
          <svg width="340" height="220" viewBox="0 0 340 220">
            <line x1="20" y1="180" x2="320" y2="180" stroke={CHALK.muted} strokeWidth="4" />
            <g transform={`translate(${80 + ballOffset}, 140)`}>
              <circle cx="0" cy="0" r="32" fill={CHALK.white} stroke={CHALK.cyan} strokeWidth="5" />
              <text x="0" y="8" fontSize="18" fontWeight="800" fill={CHALK.bg} textAnchor="middle">
                0.4 kg
              </text>
              {/* Push Arrow */}
              <line x1="-80" y1="0" x2="-40" y2="0" stroke={CHALK.gold} strokeWidth="6" />
              <polygon points="-40,-6 -28,0 -40,6" fill={CHALK.gold} />
              <text x="-60" y="-12" fontSize="16" fontWeight="700" fill={CHALK.gold} textAnchor="middle">
                F = 50N
              </text>
              {/* Big Acceleration Arrow */}
              <line x1="42" y1="0" x2="110" y2="0" stroke={CHALK.green} strokeWidth="8" />
              <polygon points="110,-10 128,0 110,10" fill={CHALK.green} />
            </g>
          </svg>
          <div style={{ fontSize: 22, fontWeight: 800, color: CHALK.green }}>
            MASSIVE ACCELERATION! 🚀
          </div>
          <div style={{ fontSize: 18, color: CHALK.muted, marginTop: 4 }}>
            Small Mass ⟹ Large 'a'
          </div>
        </div>

        {/* Right Card: Heavy Boulder */}
        <div
          style={{
            flex: 1,
            backgroundColor: CHALK.boardSurface,
            borderRadius: 20,
            border: `2px solid ${CHALK.coral}`,
            padding: "20px 16px",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 24, fontWeight: 800, color: CHALK.coral, marginBottom: 12 }}>
            HEAVY BOULDER
          </div>
          <svg width="340" height="220" viewBox="0 0 340 220">
            <line x1="20" y1="180" x2="320" y2="180" stroke={CHALK.muted} strokeWidth="4" />
            <g transform={`translate(${110 + boulderOffset}, 125)`}>
              <path
                d="M-55 50 Q-65 -40 0 -55 Q65 -40 55 50 Z"
                fill="rgba(248, 113, 113, 0.3)"
                stroke={CHALK.coral}
                strokeWidth="5"
              />
              <text x="0" y="10" fontSize="20" fontWeight="900" fill={CHALK.white} textAnchor="middle">
                400 kg
              </text>
              {/* Push Arrow */}
              <line x1="-105" y1="0" x2="-65" y2="0" stroke={CHALK.gold} strokeWidth="6" />
              <polygon points="-65,-6 -53,0 -65,6" fill={CHALK.gold} />
              <text x="-85" y="-12" fontSize="16" fontWeight="700" fill={CHALK.gold} textAnchor="middle">
                F = 50N
              </text>
              {/* Tiny Acceleration Arrow */}
              <line x1="65" y1="0" x2="85" y2="0" stroke={CHALK.coral} strokeWidth="4" />
              <polygon points="85,-5 95,0 85,5" fill={CHALK.coral} />
            </g>
          </svg>
          <div style={{ fontSize: 22, fontWeight: 800, color: CHALK.coral }}>
            TINY ACCELERATION! 🐌
          </div>
          <div style={{ fontSize: 18, color: CHALK.muted, marginTop: 4 }}>
            Huge Mass ⟹ Resists Motion
          </div>
        </div>
      </div>

      {/* Summary Box */}
      <div
        style={{
          marginTop: 20,
          backgroundColor: "rgba(56, 189, 248, 0.1)",
          border: `1px solid ${CHALK.cyan}`,
          borderRadius: 16,
          padding: "12px 24px",
          fontSize: 22,
          fontWeight: 700,
          color: CHALK.white,
          textAlign: "center",
        }}
      >
        Takeaway: Mass is simply <span style={{ color: CHALK.gold }}>inertia you can measure</span>.
      </div>

      <SubtitleCard text="Law Two: Force equals mass times acceleration. Acceleration is force divided by mass. Kick a light soccer ball with 50 Newtons, it flies! Kick a heavy boulder with the same force, its mass resists acceleration." />
      <Audio src={staticFile("audio/newtons-laws/sec-3.mp3")} />
    </ChalkboardCanvas>
  );
};

// ---------------------------------------------------------------------------
// SCENE 4: Newton's Third Law — Action & Reaction (43.61s - 61.83s = 546 frames)
// ---------------------------------------------------------------------------
const Scene4ThirdLaw: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const flamePulse = 1 + Math.sin(frame / 4) * 0.15;
  const rocketY = Math.sin(frame / 12) * 8;
  const trapSpring = spring({ frame: frame - 60, fps, config: { damping: 12 } });

  return (
    <ChalkboardCanvas sceneTag="LAW 3 · ACTION & REACTION">
      <div style={{ textAlign: "center", marginBottom: 16 }}>
        <h2 style={{ fontSize: 48, fontWeight: 900, color: CHALK.gold, margin: "0 0 10px" }}>
          LAW 3: ACTION & REACTION
        </h2>
        <div style={{ fontSize: 26, fontWeight: 700, color: CHALK.white }}>
          "Every action has an equal and opposite reaction."
        </div>
      </div>

      {/* Rocket Vector Diagram */}
      <svg width="860" height="420" viewBox="0 0 860 420">
        <g transform={`translate(320, ${210 + rocketY})`}>
          {/* Upward Thrust Arrow */}
          <line x1="0" y1="-80" x2="0" y2="-170" stroke={CHALK.cyan} strokeWidth="8" strokeLinecap="round" />
          <polygon points="-12,-170 0,-192 12,-170" fill={CHALK.cyan} />
          <text x="24" y="-130" fill={CHALK.cyan} fontSize="22" fontWeight="800">
            F(gas on rocket) ↑
          </text>

          {/* Rocket Body */}
          <path d="M-30 40 L-30 -40 Q0 -90 30 -40 L30 40 Z" fill="rgba(56, 189, 248, 0.25)" stroke={CHALK.white} strokeWidth="5" />
          <polygon points="-30,30 -50,55 -30,50" fill={CHALK.cyan} />
          <polygon points="30,30 50,55 30,50" fill={CHALK.cyan} />
          <circle cx="0" cy="-20" r="14" fill={CHALK.gold} />

          {/* Downward Exhaust Arrow */}
          <g transform={`scale(${flamePulse})`}>
            <polygon points="-20,44 0,95 20,44" fill={CHALK.coral} opacity="0.9" />
            <polygon points="-10,44 0,75 10,44" fill={CHALK.gold} opacity="0.9" />
          </g>
          <line x1="0" y1="60" x2="0" y2="160" stroke={CHALK.coral} strokeWidth="8" strokeLinecap="round" />
          <polygon points="-12,160 0,182 12,160" fill={CHALK.coral} />
          <text x="24" y="125" fill={CHALK.coral} fontSize="22" fontWeight="800">
            F(rocket on gas) ↓
          </text>
        </g>

        {/* Secret Reveal Annotation on Right */}
        <g transform="translate(540, 60)">
          <rect
            x="0"
            y="0"
            width="280"
            height="210"
            rx="20"
            fill="rgba(19, 28, 38, 0.9)"
            stroke={CHALK.gold}
            strokeWidth="3"
          />
          <text x="140" y="42" fill={CHALK.gold} fontSize="22" fontWeight="900" textAnchor="middle">
            THE SECRET:
          </text>
          <text x="140" y="80" fill={CHALK.white} fontSize="20" fontWeight="700" textAnchor="middle">
            Forces act on
          </text>
          <text x="140" y="112" fill={CHALK.cyan} fontSize="22" fontWeight="900" textAnchor="middle">
            DIFFERENT OBJECTS!
          </text>
          <line x1="30" y1="130" x2="250" y2="130" stroke={CHALK.border} strokeWidth="2" />
          <text x="140" y="165" fill={CHALK.coral} fontSize="19" fontWeight="800" textAnchor="middle">
            They NEVER cancel out!
          </text>
        </g>
      </svg>

      {/* Critical Lesson Card */}
      <div
        style={{
          opacity: trapSpring,
          transform: `scale(${trapSpring})`,
          backgroundColor: "rgba(251, 191, 36, 0.12)",
          border: `2px solid ${CHALK.gold}`,
          borderRadius: 18,
          padding: "14px 28px",
          textAlign: "center",
          maxWidth: 720,
        }}
      >
        <span style={{ fontSize: 24, fontWeight: 800, color: CHALK.gold }}>
          ⚡ Why don't they cancel?
        </span>{" "}
        <span style={{ fontSize: 22, fontWeight: 600, color: CHALK.white }}>
          Because one force acts on the gas, and the other force acts on the rocket!
        </span>
      </div>

      <SubtitleCard text="Law Three: Action and Reaction. Every action has an equal, opposite reaction. The secret? They act on different objects! A rocket pushes exhaust gas down; the gas pushes the rocket up. They never cancel out!" />
      <Audio src={staticFile("audio/newtons-laws/sec-4.mp3")} />
    </ChalkboardCanvas>
  );
};

// ---------------------------------------------------------------------------
// SCENE 5: The Golden Rule (61.83s - 69.17s = 220 frames)
// ---------------------------------------------------------------------------
const Scene5GoldenRule: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = spring({ frame, fps, config: { damping: 12 } });

  return (
    <ChalkboardCanvas sceneTag="GOLDEN RULE">
      <div
        style={{
          transform: `scale(${pop})`,
          backgroundColor: CHALK.boardSurface,
          border: `4px solid ${CHALK.gold}`,
          borderRadius: 32,
          padding: "48px 40px",
          textAlign: "center",
          maxWidth: 760,
          boxShadow: `0 0 60px rgba(251, 191, 36, 0.25)`,
        }}
      >
        <div
          style={{
            fontSize: 32,
            fontWeight: 900,
            letterSpacing: 2,
            color: CHALK.gold,
            marginBottom: 28,
            textTransform: "uppercase",
          }}
        >
          🏆 THE GOLDEN RULE OF MECHANICS
        </div>

        <div
          style={{
            backgroundColor: "rgba(248, 113, 113, 0.15)",
            border: `2px solid ${CHALK.coral}`,
            borderRadius: 16,
            padding: "18px 24px",
            fontSize: 28,
            fontWeight: 800,
            color: CHALK.coral,
            marginBottom: 20,
          }}
        >
          ❌ Forces do NOT cause motion!
        </div>

        <div
          style={{
            backgroundColor: "rgba(52, 211, 153, 0.15)",
            border: `2px solid ${CHALK.green}`,
            borderRadius: 16,
            padding: "24px 28px",
            fontSize: 32,
            fontWeight: 900,
            color: CHALK.green,
            lineHeight: 1.3,
          }}
        >
          ✅ Net UNBALANCED forces cause CHANGES in motion!
        </div>

        <div style={{ marginTop: 24, fontSize: 22, color: CHALK.muted }}>
          Keep this rule in mind, and you will never miss a physics exam question again.
        </div>
      </div>

      <SubtitleCard text="The golden rule: Forces do not cause motion. Unbalanced forces cause changes in motion!" />
      <Audio src={staticFile("audio/newtons-laws/sec-5.mp3")} />
    </ChalkboardCanvas>
  );
};

// ---------------------------------------------------------------------------
// SCENE 6: Real-World Inertia & Outro (69.17s - 76.66s = 225 frames)
// ---------------------------------------------------------------------------
const Scene6Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 14 } });

  // Car passenger braking tilt
  const lurch = interpolate(frame, [15, 60, 110], [0, 18, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <ChalkboardCanvas sceneTag="OUTRO · TAKEAWAY">
      <div style={{ textAlign: "center", marginBottom: 30 }}>
        <h2 style={{ fontSize: 50, fontWeight: 900, color: CHALK.cyan, margin: "0 0 12px" }}>
          INERTIA IN YOUR CAR
        </h2>
        <div style={{ fontSize: 26, fontWeight: 600, color: CHALK.muted }}>
          Why you lurch forward when the brakes slam
        </div>
      </div>

      {/* SVG Car Seat & Passenger */}
      <svg width="600" height="260" viewBox="0 0 600 260">
        {/* Car Floor */}
        <line x1="80" y1="220" x2="520" y2="220" stroke={CHALK.muted} strokeWidth="5" />
        {/* Car Seat */}
        <path d="M220 220 L300 220 L320 120 L380 60" stroke={CHALK.white} strokeWidth="6" fill="none" />
        {/* Passenger Body with Lurch Tilt */}
        <g transform={`translate(280, 140) rotate(${lurch})`}>
          {/* Head */}
          <circle cx="20" cy="-55" r="24" fill={CHALK.gold} />
          {/* Body */}
          <line x1="0" y1="-30" x2="20" y2="40" stroke={CHALK.white} strokeWidth="8" strokeLinecap="round" />
          {/* Seatbelt */}
          <line x1="-30" y1="-25" x2="40" y2="35" stroke={CHALK.coral} strokeWidth="6" />
          {/* Forward Inertia Arrow */}
          <line x1="45" y1="-45" x2="115" y2="-45" stroke={CHALK.cyan} strokeWidth="6" />
          <polygon points="115,-51 127,-45 115,-39" fill={CHALK.cyan} />
          <text x="85" y="-58" fill={CHALK.cyan} fontSize="18" fontWeight="800" textAnchor="middle">
            INERTIA →
          </text>
        </g>
      </svg>

      {/* Follow CTA Box */}
      <div
        style={{
          transform: `scale(${enter})`,
          backgroundColor: CHALK.boardSurface,
          border: `2px solid ${CHALK.cyan}`,
          borderRadius: 24,
          padding: "24px 44px",
          textAlign: "center",
          maxWidth: 640,
        }}
      >
        <div style={{ fontSize: 32, fontWeight: 900, color: CHALK.white, marginBottom: 8 }}>
          🚀 Physics Made Simple
        </div>
        <div style={{ fontSize: 24, fontWeight: 700, color: CHALK.gold }}>
          Subscribe & Follow for More Visual Science!
        </div>
      </div>

      <SubtitleCard text="Next time you brake in a car and lurch forward, thank inertia! Follow for more physics made simple." />
      <Audio src={staticFile("audio/newtons-laws/sec-6.mp3")} />
    </ChalkboardCanvas>
  );
};

// ---------------------------------------------------------------------------
// Main Composition: NewtonsLawsChalkboard
// ---------------------------------------------------------------------------
export const NewtonsLawsChalkboard: React.FC = () => {
  // 30fps frames:
  // sec-1: 12.26s = 368 frames (0 to 367)
  // sec-2: 14.21s = 426 frames (368 to 793)
  // sec-3: 17.14s = 514 frames (794 to 1307)
  // sec-4: 18.22s = 547 frames (1308 to 1854)
  // sec-5: 7.34s  = 220 frames (1855 to 2074)
  // sec-6: 7.49s  = 225 frames (2075 to 2299)
  // Total = 2300 frames (76.66 seconds)

  return (
    <AbsoluteFill style={{ backgroundColor: CHALK.bg }}>
      <Sequence from={0} durationInFrames={368} name="Scene 1: Hook & Friction">
        <Scene1Hook />
      </Sequence>
      <Sequence from={368} durationInFrames={426} name="Scene 2: Law 1 Inertia">
        <Scene2Inertia />
      </Sequence>
      <Sequence from={794} durationInFrames={514} name="Scene 3: Law 2 F=ma">
        <Scene3SecondLaw />
      </Sequence>
      <Sequence from={1308} durationInFrames={547} name="Scene 4: Law 3 Action Reaction">
        <Scene4ThirdLaw />
      </Sequence>
      <Sequence from={1855} durationInFrames={220} name="Scene 5: Golden Rule">
        <Scene5GoldenRule />
      </Sequence>
      <Sequence from={2075} durationInFrames={225} name="Scene 6: Real-World Inertia & Outro">
        <Scene6Outro />
      </Sequence>
    </AbsoluteFill>
  );
};
