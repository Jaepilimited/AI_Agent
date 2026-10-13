import React from "react";
import { interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { BG, C, FONT } from "../tokens";

export const IntroScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const logoSc  = spring({ frame, fps, config: { damping: 16, stiffness: 140 } });
  const lineW   = interpolate(frame, [18, 52], [0, 360], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const titleOp = interpolate(frame, [24, 44], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const titleY  = interpolate(frame, [24, 44], [20, 0], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const subOp   = interpolate(frame, [40, 58], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const tagOp   = interpolate(frame, [54, 70], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center",
      gap:22,
    }}>
      {/* Logo */}
      <div style={{ transform:`scale(${logoSc})` }}>
        <img
          src={staticFile("splash.png")}
          style={{ width:100, height:"auto", filter:"drop-shadow(0 0 20px #e8920044)" }}
        />
      </div>

      {/* Divider line */}
      <div style={{ width: lineW, height:1.5, background: C.orange, borderRadius:1 }} />

      {/* Title */}
      <div style={{ opacity: titleOp, transform:`translateY(${titleY}px)`, textAlign:"center", lineHeight:1.15 }}>
        <div style={{ fontSize:76, fontWeight:900, color: C.white, letterSpacing:-2 }}>
          SKIN1004 <span style={{ color: C.orange }}>AI</span>
        </div>
        <div style={{ fontSize:76, fontWeight:900, color: C.white, letterSpacing:-2 }}>
          에이전트
        </div>
      </div>

      {/* Subtitle */}
      <div style={{ opacity: subOp, fontSize:22, color: C.gray400, letterSpacing:4, fontWeight:300 }}>
        사용 가이드
      </div>

      {/* Tags */}
      <div style={{ opacity: tagOp, display:"flex", gap:12, marginTop:4 }}>
        {["어떻게 쓰나요", "무엇이 다른가요", "어떻게 만들어졌나요"].map((t,i) => (
          <div key={i} style={{
            fontSize:15, color: C.gray600, padding:"5px 16px",
            border:`1px solid ${C.cardBorder}`, borderRadius:20,
          }}>{t}</div>
        ))}
      </div>
    </div>
  );
};
