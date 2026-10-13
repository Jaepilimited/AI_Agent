import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";

const fade = (frame: number, start: number, dur = 16) =>
  interpolate(frame, [start, start + dur], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });

const AGENTS = [
  { label:"BigQuery",  sub:"SQL·분석·차트", col: C.blue,   delay:120 },
  { label:"Notion",    sub:"페이지·시트",   col: C.purple, delay:140 },
  { label:"GWS",       sub:"Gmail·Calendar",col: C.green,  delay:160 },
  { label:"CS",        sub:"제품 739건",    col: C.orange, delay:180 },
];

const Box: React.FC<{ op: number; title: string; sub: string; col?: string; accent?: boolean }> = ({ op, title, sub, col = C.cardBorder, accent }) => (
  <div style={{
    opacity: op,
    background: C.card, border:`1px solid ${col}`,
    borderTop: accent ? `2px solid ${col}` : undefined,
    borderRadius:10, padding:"13px 32px",
    textAlign:"center", minWidth:260,
  }}>
    <div style={{ fontSize:15, fontWeight:700, color: accent ? col : C.white }}>{title}</div>
    <div style={{ fontSize:12, color: C.gray600, marginTop:3 }}>{sub}</div>
  </div>
);

const Arrow: React.FC<{ op: number }> = ({ op }) => (
  <div style={{ opacity: op, display:"flex", flexDirection:"column", alignItems:"center" }}>
    <div style={{ width:1.5, height:18, background: C.cardBorder }} />
    <div style={{ width:0, height:0, borderLeft:"5px solid transparent", borderRight:"5px solid transparent", borderTop:`6px solid ${C.cardBorder}` }} />
  </div>
);

export const ArchitectureScene: React.FC = () => {
  const frame = useCurrentFrame();

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      boxSizing:"border-box", display:"flex", flexDirection:"column", padding:"36px 72px",
    }}>
      <div style={{ opacity: fade(frame, 0), display:"flex", alignItems:"center", gap:16, marginBottom:24, flexShrink:0 }}>
        <div style={{ width:3, height:32, background: C.orange, borderRadius:2 }} />
        <div style={{ fontSize:32, fontWeight:800, color: C.white }}>어떻게 동작하나요?</div>
      </div>

      <div style={{ flex:1, display:"flex", gap:40, overflow:"hidden" }}>
        {/* Flow diagram */}
        <div style={{ flex:1, display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", gap:0 }}>
          <Box op={fade(frame,14)} title="사용자 (임직원)" sub="172.16.1.250:3000" col={C.cardBorder} />
          <Arrow op={fade(frame,36)} />
          <Box op={fade(frame,48)} title="FastAPI 서버" sub="JWT 인증 · SSE 스트리밍 · MariaDB" col={C.cardBorder} />
          <Arrow op={fade(frame,68)} />
          <Box op={fade(frame,82)} title="Orchestrator" sub="Gemini Flash 라우팅 · LangGraph" col={C.orange} accent />
          <Arrow op={fade(frame,104)} />

          <div style={{ display:"flex", gap:12, marginTop:2 }}>
            {AGENTS.map((ag, i) => {
              const op = fade(frame, ag.delay);
              const y = interpolate(frame,[ag.delay,ag.delay+16],[14,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
              return (
                <div key={i} style={{
                  opacity: op, transform:`translateY(${y}px)`,
                  background: C.card, border:`1px solid ${ag.col}44`,
                  borderTop:`2px solid ${ag.col}`,
                  borderRadius:8, padding:"11px 16px",
                  textAlign:"center", minWidth:128,
                }}>
                  <div style={{ fontSize:14, fontWeight:700, color: ag.col }}>{ag.label}</div>
                  <div style={{ fontSize:11, color: C.gray600, marginTop:3 }}>{ag.sub}</div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Notes */}
        <div style={{ width:310, flexShrink:0, display:"flex", flexDirection:"column", gap:12, justifyContent:"center" }}>
          {[
            { delay:48,  col:C.blue,   title:"AD 인증 로그인", body:"Active Directory 자동 동기화. JWT httpOnly 쿠키 (7일)" },
            { delay:76,  col:C.purple, title:"Dual LLM 엔진", body:"Gemini 2.5 Pro + Claude Sonnet. 용도별 자동 전환" },
            { delay:106, col:C.orange, title:"스마트 라우팅", body:"Gemini Flash가 질문 분석 → bigquery / notion / gws / cs / direct 분기" },
            { delay:198, col:C.green,  title:"보안 격리", body:"사내망 전용 서버. 외부 전송 없음. MariaDB 로컬 저장" },
          ].map((note, i) => (
            <div key={i} style={{
              opacity: fade(frame, note.delay),
              background: C.card, border:`1px solid ${C.cardBorder}`,
              borderRadius:8, padding:"12px 16px",
              borderLeft:`3px solid ${note.col}`,
            }}>
              <div style={{ fontSize:13, fontWeight:700, color: note.col, marginBottom:4 }}>{note.title}</div>
              <div style={{ fontSize:12, color: C.gray400, lineHeight:1.65 }}>{note.body}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
