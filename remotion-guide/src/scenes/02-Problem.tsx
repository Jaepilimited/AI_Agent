import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";

// Each task card appears at its delay frame
const TASKS = [
  { label:"매출 데이터 조회",   sub:"BigQuery SQL 직접 작성",  time:"2h",   delay:30 },
  { label:"제품 CS 확인",      sub:"스프레드시트 탭 13개 순회", time:"20min", delay:60 },
  { label:"일정·메일 파악",    sub:"Gmail + Calendar 앱 전환",  time:"15min", delay:90 },
  { label:"ChatGPT 시도",      sub:"사내 데이터 없음 + 보안 위험", time:"불가", delay:120 },
];

export const ProblemScene: React.FC = () => {
  const frame = useCurrentFrame();

  const headOp = interpolate(frame, [0, 20], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const headY  = interpolate(frame, [0, 20], [16, 0], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const solveOp = interpolate(frame, [160, 178], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const solveY  = interpolate(frame, [160, 178], [14, 0], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center",
      gap:0, padding:"0 0 40px",
    }}>
      {/* Heading */}
      <div style={{
        opacity: headOp, transform:`translateY(${headY}px)`,
        fontSize:32, color: C.gray400, fontWeight:400, letterSpacing:1, marginBottom:40,
      }}>
        AI 없이 업무하면...
      </div>

      {/* Task cards — appear one by one */}
      <div style={{ display:"flex", flexDirection:"column", gap:14, width:640 }}>
        {TASKS.map((t, i) => {
          const op = interpolate(frame, [t.delay, t.delay + 16], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
          const x  = interpolate(frame, [t.delay, t.delay + 16], [-24, 0], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
          const isLast = t.time === "불가";
          return (
            <div key={i} style={{
              opacity: op, transform:`translateX(${x}px)`,
              display:"flex", alignItems:"center",
              background: C.card, border:`1px solid ${isLast ? C.red + "44" : C.cardBorder}`,
              borderRadius:10, padding:"16px 20px", gap:16,
            }}>
              <div style={{
                width:28, height:28, borderRadius:"50%",
                background:`${C.cardBorder}`, border:`1px solid ${C.gray600}`,
                display:"flex", alignItems:"center", justifyContent:"center",
                fontSize:13, color: C.gray600, fontWeight:700, flexShrink:0,
              }}>{i + 1}</div>
              <div style={{ flex:1 }}>
                <div style={{ fontSize:17, fontWeight:700, color: C.white }}>{t.label}</div>
                <div style={{ fontSize:13, color: C.gray400, marginTop:3 }}>{t.sub}</div>
              </div>
              <div style={{
                fontSize:15, fontWeight:700,
                color: isLast ? C.red : C.orange,
                padding:"4px 12px", borderRadius:6,
                background: isLast ? `${C.red}15` : `${C.orange}15`,
                border:`1px solid ${isLast ? C.red + "44" : C.orange + "44"}`,
                whiteSpace:"nowrap", flexShrink:0,
              }}>{t.time}</div>
            </div>
          );
        })}
      </div>

      {/* Solution callout */}
      <div style={{
        opacity: solveOp, transform:`translateY(${solveY}px)`,
        marginTop:36, width:640, textAlign:"center",
        padding:"14px 24px",
        background:`${C.orange}0e`, border:`1px solid ${C.orange}55`,
        borderRadius:10,
        fontSize:18, color: C.orange, fontWeight:700, letterSpacing:0.5,
      }}>
        SKIN1004 AI 에이전트로는 — 전부 30초
      </div>
    </div>
  );
};
