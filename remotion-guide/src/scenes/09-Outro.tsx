import React from "react";
import { interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { BG, C, FONT } from "../tokens";

export const OutroScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const logoSc  = spring({ frame, fps, config:{ damping:16, stiffness:150 } });
  const lineW   = interpolate(frame,[14,52],[0,340],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const titleOp = interpolate(frame,[20,38],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const titleY  = interpolate(frame,[20,38],[18,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const subOp   = interpolate(frame,[34,50],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  const CHIPS = ["172.16.1.250:3000 접속","이름·비밀번호 입력","질문 시작"];
  const chipDelay = [48, 62, 76];

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center",
      gap:22,
    }}>
      <div style={{ transform:`scale(${logoSc})` }}>
        <img src={staticFile("splash.png")} style={{ width:88, height:"auto", filter:"drop-shadow(0 0 18px #e8920044)" }} />
      </div>

      <div style={{ width:lineW, height:1.5, background: C.orange, borderRadius:1 }} />

      <div style={{ opacity:titleOp, transform:`translateY(${titleY}px)`, textAlign:"center", lineHeight:1.2 }}>
        <div style={{ fontSize:58, fontWeight:900, color:C.white, letterSpacing:-1 }}>지금 바로 시작하세요</div>
        <div style={{ fontSize:58, fontWeight:900, color:C.orange, letterSpacing:-1 }}>AI가 일하는 방식을 바꿉니다</div>
      </div>

      <div style={{ opacity:subOp, fontSize:18, color:C.gray400, letterSpacing:2, fontWeight:300 }}>
        SKIN1004 임직원 누구나 · 별도 설치 없음 · 사내망 전용
      </div>

      <div style={{ display:"flex", gap:12, marginTop:4 }}>
        {CHIPS.map((c, i) => {
          const op = interpolate(frame,[chipDelay[i],chipDelay[i]+14],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
          const y  = interpolate(frame,[chipDelay[i],chipDelay[i]+14],[10,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
          return (
            <div key={i} style={{
              opacity:op, transform:`translateY(${y}px)`,
              background: i===2 ? C.orange : C.card,
              border:`1px solid ${i===2 ? C.orange : C.cardBorder}`,
              borderRadius:24, padding:"10px 24px",
              fontSize:16, fontWeight:700,
              color: i===2 ? C.dark : C.white,
            }}>{c}</div>
          );
        })}
      </div>

      <div style={{
        opacity: interpolate(frame,[90,106],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"}),
        fontSize:13, color:C.gray600,
      }}>문의: jeffrey@skin1004korea.com</div>
    </div>
  );
};
