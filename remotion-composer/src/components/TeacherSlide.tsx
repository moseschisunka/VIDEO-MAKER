import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type TeacherSlideData = {
  title: string;
  section: string;
  bullets: string[];
  diagram: string;
  keyTakeaway?: string;
  diagramExplanation?: string;
  visualTarget?: string;
  slideNumber?: number;
  slideCount?: number;
  visualVariant?: "balanced-grid" | "diagram-focus" | "minimal-lecture";
};

type TeacherSlideProps = {
  slide: TeacherSlideData;
  backgroundColor?: string;
  surfaceColor?: string;
  accentColor?: string;
  textColor?: string;
  mutedColor?: string;
};

const DEFAULTS = {
  background: "#061F18",
  surface: "#0C3B2D",
  accent: "#A7F36B",
  text: "#F3F8F1",
  muted: "#BBD0C2",
  ink: "#09251D",
};

const reveal = (frame: number, start: number, duration = 18) =>
  interpolate(frame, [start, start + duration], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

const svgText = (
  x: number,
  y: number,
  value: string,
  fill: string,
  size = 22,
  anchor: "start" | "middle" | "end" = "middle",
  weight = 700,
) => (
  <text
    x={x}
    y={y}
    fill={fill}
    textAnchor={anchor}
    fontFamily="Arial, sans-serif"
    fontSize={size}
    fontWeight={weight}
    letterSpacing={size >= 20 ? 0.3 : 0}
  >
    {value}
  </text>
);

const Diagram: React.FC<{
  kind: string;
  accent: string;
  text: string;
  muted: string;
  ink: string;
}> = ({ kind, accent, text, muted, ink }) => {
  const frame = useCurrentFrame();
  const draw = reveal(frame, 12, 30);
  const second = reveal(frame, 34, 24);
  const third = reveal(frame, 58, 24);
  const pulse = 1 + Math.sin(Math.max(0, frame - 90) / 18) * 0.012;
  const lineProps = {
    fill: "none",
    stroke: accent,
    strokeWidth: 6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeDasharray: 520,
    strokeDashoffset: 520 * (1 - draw),
  };
  const node = (cx: number, cy: number, label: string, opacity = 1) => (
    <g opacity={opacity}>
      <circle cx={cx} cy={cy} r={43} fill={`${accent}22`} stroke={accent} strokeWidth={3} />
      <circle cx={cx} cy={cy} r={25} fill={accent} />
      {svgText(cx, cy + 82, label, text, 17)}
    </g>
  );
  const bar = (x: number, base: number, height: number, opacity = 1, scale = 1) => (
    <rect
      x={x}
      y={base - height * scale}
      width={48}
      height={height * scale}
      rx={10}
      fill={accent}
      opacity={opacity}
    />
  );

  if (kind === "population") {
    const people = [
      [174, 156], [284, 128], [394, 156], [228, 264], [340, 264], [450, 238],
    ];
    return (
      <g>
        {svgText(310, 45, "POPULATION", accent, 20)}
        <path d="M174 330 H456" {...lineProps} />
        {people.map(([x, y], i) => (
          <g key={`${x}-${y}`} opacity={reveal(frame, 16 + i * 8, 18)}>
            <circle cx={x} cy={y} r={28} fill={accent} opacity={0.9 - i * 0.08} />
            <path d={`M${x - 26} ${y + 66} Q${x} ${y + 20} ${x + 26} ${y + 66}`} fill={`${accent}33`} stroke={accent} strokeWidth={5} />
          </g>
        ))}
        {svgText(310, 408, "patterns become evidence", text, 22)}
      </g>
    );
  }

  if (kind === "questions") {
    return (
      <g>
        <path d="M310 188 L150 300 M310 188 L470 300 M310 188 L310 300" {...lineProps} />
        <circle cx={310} cy={188} r={54} fill={`${accent}2A`} stroke={accent} strokeWidth={4} transform={`scale(${pulse})`} transformOrigin="310px 188px" />
        {svgText(310, 195, "PATTERN", text, 17)}
        {[{ x: 80, label: "WHO" }, { x: 400, label: "WHERE" }, { x: 240, label: "WHEN" }].map((item, i) => (
          <g key={item.label} opacity={reveal(frame, 34 + i * 13, 20)}>
            <rect x={item.x} y={292} width={140} height={74} rx={18} fill={`${accent}18`} stroke={accent} strokeWidth={3} />
            {svgText(item.x + 70, 338, item.label, accent, 20)}
          </g>
        ))}
      </g>
    );
  }

  if (kind === "three_part") {
    return (
      <g>
        <path d="M310 74 L116 350 H504 Z" fill={`${accent}0D`} stroke={accent} strokeWidth={6} strokeDasharray={520} strokeDashoffset={520 * (1 - draw)} />
        {node(310, 74, "PERSON", draw)}
        {node(116, 350, "PLACE", second)}
        {node(504, 350, "TIME", third)}
        <circle cx={310} cy={260} r={46} fill={accent} opacity={0.16 + second * 0.12} />
        {svgText(310, 267, "PATTERN", accent, 20)}
      </g>
    );
  }

  if (kind === "case_definition") {
    return (
      <g>
        <path d="M90 90 H530 L438 188 H182 Z" fill={`${accent}16`} stroke={accent} strokeWidth={4} opacity={draw} />
        <path d="M182 188 H438 L392 306 H228 Z" fill={`${accent}24`} stroke={accent} strokeWidth={4} opacity={second} />
        <path d="M228 306 H392 L360 382 H260 Z" fill={`${accent}38`} stroke={accent} strokeWidth={4} opacity={third} />
        {svgText(310, 145, "criteria", accent, 19)}
        {svgText(310, 250, "shared rule", accent, 19)}
        {svgText(310, 355, "CASE", text, 22)}
      </g>
    );
  }

  if (kind === "count_rates") {
    return (
      <g>
        <path d="M90 382 H540 M90 382 V68" stroke={muted} strokeWidth={3} />
        {[70, 125, 180, 240].map((height, i) => bar(136 + i * 92, 382, height, reveal(frame, 18 + i * 10, 24), reveal(frame, 18 + i * 10, 24)))}
        {svgText(310, 426, "cases", text, 18)}
        <rect x={145} y={30} width={330} height={52} rx={26} fill={`${accent}18`} stroke={accent} strokeWidth={2} />
        {svgText(310, 64, "count → population → rate", accent, 18)}
      </g>
    );
  }

  if (kind === "incidence") {
    return (
      <g>
        <path d="M86 320 H540 M86 320 V70" stroke={muted} strokeWidth={3} />
        <path d="M108 298 C176 250 208 278 272 212 S388 156 520 90" {...lineProps} />
        {[{ x: 150, y: 266 }, { x: 258, y: 221 }, { x: 374, y: 169 }, { x: 486, y: 112 }].map((point, i) => (
          <circle key={point.x} cx={point.x} cy={point.y} r={15} fill={accent} opacity={reveal(frame, 24 + i * 12, 18)} />
        ))}
        {svgText(310, 400, "NEW CASES · OVER TIME", accent, 21)}
      </g>
    );
  }

  if (kind === "prevalence") {
    const people = Array.from({ length: 8 }, (_, i) => ({ x: 142 + (i % 4) * 98, y: 135 + Math.floor(i / 4) * 142, affected: [0, 2, 5, 7].includes(i) }));
    return (
      <g>
        <rect x={82} y={58} width={456} height={310} rx={28} fill={`${accent}0D`} stroke={accent} strokeWidth={4} />
        {people.map((person, i) => (
          <g key={`${person.x}-${person.y}`} opacity={reveal(frame, 18 + i * 7, 18)}>
            <circle cx={person.x} cy={person.y} r={22} fill={person.affected ? accent : muted} />
            <path d={`M${person.x - 22} ${person.y + 54} Q${person.x} ${person.y + 12} ${person.x + 22} ${person.y + 54}`} fill={person.affected ? `${accent}88` : `${muted}44`} stroke={person.affected ? accent : muted} strokeWidth={4} />
          </g>
        ))}
        {svgText(310, 416, "EVERYONE WITH THE CONDITION", accent, 19)}
      </g>
    );
  }

  if (kind === "denominator") {
    return (
      <g>
        <rect x={92} y={48} width={436} height={90} rx={24} fill={`${accent}1C`} stroke={accent} strokeWidth={3} />
        <rect x={92} y={286} width={436} height={90} rx={24} fill={`${accent}1C`} stroke={accent} strokeWidth={3} />
        {svgText(310, 104, "CASES", accent, 28)}
        <path d="M100 222 H520" stroke={text} strokeWidth={7} strokeLinecap="round" />
        {svgText(310, 343, "POPULATION", accent, 28)}
        <path d="M310 142 V198 M310 244 V286" {...lineProps} />
        {svgText(310, 422, "the denominator adds context", text, 20)}
      </g>
    );
  }

  if (kind === "outbreak") {
    return (
      <g>
        <path d="M78 168 C190 132 300 178 538 140" fill="none" stroke={muted} strokeWidth={4} strokeDasharray="12 12" opacity={0.9} />
        <path d="M86 382 H540 M86 382 V66" stroke={muted} strokeWidth={3} />
        {[58, 74, 90, 118, 210, 286].map((height, i) => bar(122 + i * 65, 382, height, reveal(frame, 16 + i * 8, 22), reveal(frame, 16 + i * 8, 22)))}
        {svgText(310, 58, "expected", muted, 18)}
        {svgText(420, 426, "observed rises above expected", accent, 18)}
      </g>
    );
  }

  if (kind === "descriptive") {
    const columns = ["TIME", "PLACE", "PERSON"];
    return (
      <g>
        {columns.map((column, i) => (
          <g key={column} opacity={reveal(frame, 18 + i * 18, 22)}>
            <rect x={76 + i * 154} y={78} width={122} height={264} rx={20} fill={`${accent}${i === 1 ? "30" : "16"}`} stroke={accent} strokeWidth={3} />
            {Array.from({ length: 4 }, (_, row) => (
              <rect key={row} x={96 + i * 154} y={126 + row * 44} width={82 + ((row + i) % 2) * 24} height={12} rx={6} fill={accent} opacity={0.35 + row * 0.13} />
            ))}
            {svgText(137 + i * 154, 390, column, accent, 17)}
          </g>
        ))}
      </g>
    );
  }

  if (kind === "analytical") {
    return (
      <g>
        <rect x={48} y={138} width={190} height={136} rx={24} fill={`${accent}18`} stroke={accent} strokeWidth={4} opacity={draw} />
        <rect x={382} y={138} width={190} height={136} rx={24} fill={`${accent}18`} stroke={accent} strokeWidth={4} opacity={second} />
        {svgText(143, 216, "EXPOSURE", accent, 19)}
        {svgText(477, 216, "OUTCOME", accent, 19)}
        <path d="M245 206 H375" {...lineProps} markerEnd="url(#teacher-arrow)" />
        {svgText(310, 348, "compare groups", text, 22)}
      </g>
    );
  }

  if (kind === "risk_ratio") {
    return (
      <g>
        <path d="M74 382 H548" stroke={muted} strokeWidth={3} />
        {bar(156, 382, 160, draw, draw)}
        {bar(382, 382, 274, second, second)}
        {svgText(180, 420, "GROUP A", text, 17)}
        {svgText(406, 420, "GROUP B", text, 17)}
        <rect x={170} y={42} width={280} height={58} rx={29} fill={`${accent}18`} stroke={accent} strokeWidth={2} />
        {svgText(310, 80, "RISK A ÷ RISK B", accent, 21)}
      </g>
    );
  }

  if (kind === "bias") {
    return (
      <g>
        <circle cx={310} cy={220} r={142} fill={`${accent}0B`} stroke={accent} strokeWidth={5} opacity={draw} />
        <circle cx={310} cy={220} r={82} fill={`${accent}22`} stroke={accent} strokeWidth={4} opacity={second} />
        <circle cx={310} cy={220} r={28} fill={accent} opacity={third} />
        <path d="M92 80 L248 184 M528 82 L372 184 M92 360 L248 256" {...lineProps} />
        {svgText(310, 418, "CHECK WHAT SHIFTS THE RESULT", accent, 18)}
      </g>
    );
  }

  if (kind === "prevention") {
    return (
      <g>
        <path d="M310 52 L478 112 V244 C478 326 388 374 310 404 C232 374 142 326 142 244 V112 Z" fill={`${accent}16`} stroke={accent} strokeWidth={6} opacity={draw} />
        <path d="M222 222 L280 280 L406 150" {...lineProps} strokeWidth={15} opacity={second} />
        {svgText(310, 444, "PREVENT · MONITOR · IMPROVE", accent, 18)}
      </g>
    );
  }

  return (
    <g>
      <path d="M164 116 H456 M456 116 L528 220 L456 324 M456 324 H164 M164 324 L92 220 L164 116" {...lineProps} />
      <circle cx={164} cy={116} r={38} fill={accent} opacity={draw} />
      <circle cx={456} cy={116} r={38} fill={accent} opacity={second} />
      <circle cx={456} cy={324} r={38} fill={accent} opacity={third} />
      <circle cx={164} cy={324} r={38} fill={accent} opacity={third} />
      {svgText(310, 224, "EVIDENCE", accent, 22)}
      {svgText(310, 418, "DECISION → ACTION → MONITOR", text, 18)}
    </g>
  );
};

export const TeacherSlide: React.FC<TeacherSlideProps> = ({
  slide,
  backgroundColor = DEFAULTS.background,
  surfaceColor = DEFAULTS.surface,
  accentColor = DEFAULTS.accent,
  textColor = DEFAULTS.text,
  mutedColor = DEFAULTS.muted,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const intro = reveal(frame, 0, 22);
  const bullets = (slide.bullets || []).slice(0, 3);
  const activeBullet = Math.min(
    Math.max(0, bullets.length - 1),
    Math.floor(Math.max(0, frame - 48) / 36),
  );
  const progress = interpolate(frame, [0, Math.max(1, durationInFrames - 1)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const slideNumber = slide.slideNumber || 1;
  const slideCount = slide.slideCount || 1;
  const visualVariant = slide.visualVariant || "balanced-grid";
  const diagramFocus = visualVariant === "diagram-focus";
  const minimalLecture = visualVariant === "minimal-lecture";
  const leftWidth = diagramFocus ? 640 : minimalLecture ? 860 : 778;
  const rightWidth = diagramFocus ? 930 : minimalLecture ? 680 : 782;
  const panelBorder = minimalLecture ? `${mutedColor}20` : `${mutedColor}44`;
  const panelBackground = minimalLecture ? `${backgroundColor}B8` : `${backgroundColor}E8`;

  return (
    <AbsoluteFill style={{ background: backgroundColor, color: textColor, fontFamily: "Arial, sans-serif" }}>
      <AbsoluteFill style={{ padding: "58px 72px 52px", boxSizing: "border-box" }}>
        <div style={{
          position: "absolute", inset: 28, border: `1px solid ${accentColor}30`, borderRadius: 34,
          background: `linear-gradient(135deg, ${surfaceColor} 0%, ${backgroundColor} 72%)`,
          boxShadow: `0 24px 70px ${DEFAULTS.ink}66`,
        }} />

        <div style={{ position: "relative", height: "100%", opacity: intro }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", color: accentColor, fontSize: 21, fontWeight: 700, letterSpacing: 4 }}>
            <span>iLearnZed · VISUAL LESSON</span>
            <span style={{ color: mutedColor, letterSpacing: 2 }}>BEAT {String(slideNumber).padStart(2, "0")} / {String(slideCount).padStart(2, "0")}</span>
          </div>

          <div style={{ position: "absolute", top: 86, left: 0, width: 910 }}>
            <div style={{ color: accentColor, fontSize: 20, fontWeight: 700, letterSpacing: 4, marginBottom: 18 }}>{slide.section}</div>
            <div style={{ fontSize: 65, lineHeight: 1.05, fontWeight: 800, letterSpacing: -1.6, maxWidth: 900 }}>{slide.title}</div>
            <div style={{ marginTop: 16, color: mutedColor, fontSize: 22, lineHeight: 1.35, maxWidth: 820 }}>
              Follow the visual model, then keep the takeaway in mind.
            </div>
          </div>

          <div style={{ position: "absolute", left: 0, top: diagramFocus ? 350 : 340, width: leftWidth, height: 522, borderRadius: 26, background: panelBackground, border: `1px solid ${panelBorder}`, boxSizing: "border-box", padding: minimalLecture ? "28px 34px" : "28px 30px" }}>
            <div style={{ color: mutedColor, fontSize: 17, letterSpacing: 3, fontWeight: 700 }}>KEY POINTS</div>
            <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 14 }}>
              {bullets.map((bullet, index) => {
                const pointProgress = reveal(frame, 16 + index * 24, 20);
                const active = index === activeBullet;
                return (
                  <div key={`${bullet}-${index}`} style={{ display: "flex", gap: 18, alignItems: "flex-start", opacity: pointProgress, transform: `translateX(${(1 - pointProgress) * -18}px)`, padding: "9px 12px", borderRadius: 16, background: active ? `${accentColor}14` : "transparent", border: active ? `1px solid ${accentColor}55` : "1px solid transparent" }}>
                    <div style={{ width: 36, height: 36, borderRadius: 18, background: active ? accentColor : `${accentColor}2A`, color: active ? DEFAULTS.ink : accentColor, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 auto", fontSize: 17, fontWeight: 800 }}>{index + 1}</div>
                    <div style={{ color: textColor, fontSize: diagramFocus ? 27 : 31, lineHeight: 1.17, fontWeight: active ? 700 : 600, paddingTop: 2 }}>{bullet}</div>
                  </div>
                );
              })}
            </div>
            <div style={{ position: "absolute", left: 30, right: 30, bottom: 24, paddingTop: 17, borderTop: `1px solid ${mutedColor}38`, color: mutedColor, fontSize: 18, lineHeight: 1.3 }}>
              <span style={{ color: accentColor, fontWeight: 800, letterSpacing: 2, fontSize: 14 }}>TAKEAWAY  </span>{slide.keyTakeaway || bullets[0]}
            </div>
          </div>

          <div style={{ position: "absolute", right: 0, top: minimalLecture ? 330 : 300, width: rightWidth, height: 562, borderRadius: 26, background: panelBackground, border: `1px solid ${panelBorder}`, boxSizing: "border-box", padding: diagramFocus ? "25px 34px" : "25px 28px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ color: mutedColor, fontSize: 17, letterSpacing: 3, fontWeight: 700 }}>VISUAL MODEL</span>
              <span style={{ color: accentColor, fontSize: 14, letterSpacing: 1, fontWeight: 700 }}>{slide.visualTarget || "guided diagram"}</span>
            </div>
            <svg viewBox="0 0 620 470" style={{ width: "100%", height: 420, display: "block", marginTop: 8, overflow: "visible" }}>
              <defs>
                <marker id="teacher-arrow" markerWidth="12" markerHeight="12" refX="9" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 Z" fill={accentColor} /></marker>
              </defs>
              <Diagram kind={slide.diagram} accent={accentColor} text={textColor} muted={mutedColor} ink={DEFAULTS.ink} />
            </svg>
            <div style={{ color: mutedColor, fontSize: 17, lineHeight: 1.25, marginTop: -1, minHeight: 42, textAlign: "center" }}>
              {slide.diagramExplanation || "Read the visual from its parts to the main idea."}
            </div>
          </div>

          <div style={{ position: "absolute", left: 0, right: 0, bottom: 5, display: "flex", alignItems: "center", gap: 16, color: mutedColor, fontSize: 14, letterSpacing: 1.2 }}>
            <span>EXPLAIN · CONNECT · RECALL</span>
            <div style={{ height: 5, flex: 1, borderRadius: 3, background: `${mutedColor}28`, overflow: "hidden" }}><div style={{ width: `${progress * 100}%`, height: "100%", background: accentColor, borderRadius: 3 }} /></div>
            <span>{Math.round(progress * 100)}%</span>
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
