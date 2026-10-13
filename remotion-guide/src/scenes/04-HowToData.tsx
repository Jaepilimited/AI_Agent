import React from "react";
import { interpolate, staticFile, useCurrentFrame } from "remotion";
import { BG, C, FONT, FONT_MONO } from "../tokens";

export const HowToDataScene: React.FC = () => {
  const frame = useCurrentFrame();
  const phase = frame < 120 ? 0 : frame < 240 ? 1 : 2;

  const headOp  = interpolate(frame, [0, 18], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const imgOp   = interpolate(frame, [14, 30], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const ans2Op  = interpolate(frame, [120, 136], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const ch3Op   = interpolate(frame, [240, 256], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });

  const EXAMPLES = ["2025년 1분기 국가별 매출 현황", "지난달 TOP 10 제품은?", "큐텐 메가와리 Q2 실적 비교"];

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      boxSizing:"border-box", display:"flex", flexDirection:"column", padding:"44px 72px",
    }}>
      <div style={{ opacity: headOp, display:"flex", alignItems:"center", gap:16, marginBottom:28, flexShrink:0 }}>
        <div style={{ width:3, height:32, background: C.orange, borderRadius:2 }} />
        <div style={{ fontSize:32, fontWeight:800, color: C.white }}>데이터 조회</div>
        <div style={{ marginLeft:"auto", display:"flex", gap:8 }}>
          {["자연어","BigQuery","차트 자동생성"].map((t,i) => (
            <div key={i} style={{ fontSize:13, color: C.gray600, padding:"4px 12px", border:`1px solid ${C.cardBorder}`, borderRadius:6 }}>{t}</div>
          ))}
        </div>
      </div>

      <div style={{ flex:1, display:"flex", gap:40, overflow:"hidden" }}>
        <div style={{ width:320, flexShrink:0, display:"flex", flexDirection:"column", gap:14 }}>
          <div style={{ fontSize:13, color: C.gray600, letterSpacing:1, textTransform:"uppercase", marginBottom:2 }}>예시 질문</div>
          {EXAMPLES.map((ex, i) => {
            const d = 22 + i * 16;
            const op = interpolate(frame, [d, d + 14], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
            const active = i === 0 && phase === 0;
            return (
              <div key={i} style={{
                opacity: op,
                background: active ? `${C.orange}10` : C.card,
                border:`1px solid ${active ? C.orange + "44" : C.cardBorder}`,
                borderRadius:8, padding:"11px 16px",
                fontSize:15, color: active ? C.orange : C.white, fontWeight: active ? 600 : 400,
              }}>"{ex}"</div>
            );
          })}

          {/* Flow */}
          <div style={{ marginTop:4, display:"flex", flexDirection:"column", gap:8 }}>
            {[
              { label:"자연어 질문", col: C.orange, delay:40 },
              { label:"SQL 자동 생성·검증", col: C.blue, delay:64 },
              { label:"BigQuery 실행", col: C.purple, delay:88 },
              { label:"분석 + 차트 반환", col: C.green, delay:112 },
            ].map((s, i) => {
              const op = interpolate(frame, [s.delay, s.delay + 14], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
              return (
                <div key={i} style={{ opacity: op, display:"flex", alignItems:"center", gap:10 }}>
                  <div style={{ width:8, height:8, borderRadius:"50%", background: s.col, flexShrink:0 }} />
                  <div style={{ fontSize:14, color: C.gray400 }}>{s.label}</div>
                </div>
              );
            })}
          </div>

          {phase >= 1 && (
            <div style={{
              opacity: ans2Op,
              background: C.card, borderRadius:8, padding:"10px 12px",
              fontFamily: FONT_MONO, fontSize:11, color:"#7dd3fc",
              lineHeight:1.8, marginTop:4,
            }}>
              <div style={{ color: C.gray600, fontFamily: FONT, fontSize:11, marginBottom:4 }}>자동 생성된 SQL</div>
              {"SELECT country,\n  SUM(Sales1_R)\nFROM SALES_ALL_Backup\nWHERE YEAR=2025 AND Q=1\nGROUP BY country\nORDER BY 2 DESC"}
            </div>
          )}
        </div>

        {/* Screenshot */}
        <div style={{ flex:1, position:"relative", borderRadius:10, overflow:"hidden", border:`1px solid ${C.cardBorder}` }}>
          <div style={{ position:"absolute", inset:0, opacity: phase === 0 ? imgOp : 0 }}>
            <img src={staticFile("screenshots/07_bq_typed.png")} style={{ width:"100%", height:"100%", objectFit:"cover", objectPosition:"bottom center" }} />
            <div style={{
              position:"absolute", top:18, left:18,
              background:"rgba(14,20,32,0.9)", border:`1.5px solid ${C.orange}`,
              borderRadius:8, padding:"7px 16px",
              color: C.white, fontSize:15, fontWeight:600,
              opacity: interpolate(frame,[22,36],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"}),
            }}>Enter 키로 전송</div>
          </div>
          <div style={{ position:"absolute", inset:0, opacity: phase === 1 ? ans2Op : phase > 1 ? 1 : 0 }}>
            <img src={staticFile("screenshots/08_bq_answer.png")} style={{ width:"100%", height:"100%", objectFit:"cover", objectPosition:"top center" }} />
            {phase === 1 && (
              <div style={{
                position:"absolute", top:18, right:18,
                opacity: interpolate(frame,[136,150],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"}),
                background:"rgba(14,20,32,0.9)", border:`1.5px solid ${C.green}`,
                borderRadius:8, padding:"7px 16px",
                color: C.green, fontSize:14, fontWeight:700,
              }}>국가별 매출 + 인사이트 자동 생성</div>
            )}
          </div>
          <div style={{ position:"absolute", inset:0, opacity: phase >= 2 ? ch3Op : 0 }}>
            <img src={staticFile("screenshots/09_chart_full.png")} style={{ width:"100%", height:"100%", objectFit:"cover" }} />
            <div style={{
              position:"absolute", top:18, right:18,
              opacity: interpolate(frame,[254,268],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"}),
              background:"rgba(14,20,32,0.9)", border:`1.5px solid ${C.orange}`,
              borderRadius:8, padding:"7px 16px",
              color: C.orange, fontSize:14, fontWeight:700,
            }}>Bar · Line · Doughnut 자동 선택</div>
          </div>
        </div>
      </div>
    </div>
  );
};
