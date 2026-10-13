import React from "react";
import { interpolate, staticFile, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";

export const HowToCSScene: React.FC = () => {
  const frame = useCurrentFrame();
  const phase = frame < 110 ? 0 : 1;

  const headOp = interpolate(frame,[0,18],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const imgOp1 = interpolate(frame,[14,30],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const imgOp2 = interpolate(frame,[112,128],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  const QS = [
    "마데카솔 크림 성분·특징 알려줘",
    "건성 피부에 맞는 제품 추천해줘",
    "센텔라 앰플 사용 방법은?",
    "0.5% 세럼 주의사항 알려줘",
  ];

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      boxSizing:"border-box", display:"flex", flexDirection:"column", padding:"44px 72px",
    }}>
      <div style={{ opacity: headOp, display:"flex", alignItems:"center", gap:16, marginBottom:28, flexShrink:0 }}>
        <div style={{ width:3, height:32, background: C.orange, borderRadius:2 }} />
        <div style={{ fontSize:32, fontWeight:800, color: C.white }}>제품 CS 질문</div>
        <div style={{ marginLeft:"auto", fontSize:13, color: C.gray600, padding:"5px 14px", border:`1px solid ${C.cardBorder}`, borderRadius:6 }}>739건+ 전문 데이터</div>
      </div>

      <div style={{ flex:1, display:"flex", gap:40, overflow:"hidden" }}>
        <div style={{ width:300, flexShrink:0, display:"flex", flexDirection:"column", gap:10, justifyContent:"center" }}>
          <div style={{ fontSize:12, color: C.gray600, letterSpacing:1, textTransform:"uppercase", marginBottom:4 }}>예시 질문</div>
          {QS.map((q, i) => {
            const d = 18 + i * 18;
            const op = interpolate(frame,[d,d+14],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
            const active = i === 0;
            return (
              <div key={i} style={{
                opacity: op,
                background: active ? `${C.orange}10` : C.card,
                border:`1px solid ${active ? C.orange + "44" : C.cardBorder}`,
                borderRadius:8, padding:"10px 14px",
                color: active ? C.orange : C.white, fontSize:15, fontWeight: active ? 600 : 400,
              }}>"{q}"</div>
            );
          })}
          {phase >= 1 && (
            <div style={{
              opacity: imgOp2,
              background: C.card, border:`1px solid ${C.cardBorder}`,
              borderRadius:8, padding:"12px 14px", marginTop:6,
              borderLeft:`3px solid ${C.green}`,
            }}>
              <div style={{ fontSize:12, color: C.green, fontWeight:700, marginBottom:8, letterSpacing:0.5, textTransform:"uppercase" }}>답변 포함 항목</div>
              {["성분 및 주요 특징","적합 피부 타입","사용 방법·주의사항","출처 탭 명시"].map((t,i) => (
                <div key={i} style={{ fontSize:13, color: C.gray400, lineHeight:2, borderBottom:`1px solid ${C.cardBorder}44` }}>{t}</div>
              ))}
            </div>
          )}
        </div>

        <div style={{ flex:1, position:"relative", borderRadius:10, overflow:"hidden", border:`1px solid ${C.cardBorder}` }}>
          <div style={{ position:"absolute", inset:0, opacity: phase === 0 ? imgOp1 : 0 }}>
            <img src={staticFile("screenshots/10_cs_typed.png")} style={{ width:"100%", height:"100%", objectFit:"cover", objectPosition:"bottom center" }} />
          </div>
          <div style={{ position:"absolute", inset:0, opacity: phase >= 1 ? imgOp2 : 0 }}>
            <img src={staticFile("screenshots/11_cs_answer.png")} style={{ width:"100%", height:"100%", objectFit:"cover", objectPosition:"top center" }} />
          </div>
        </div>
      </div>
    </div>
  );
};
