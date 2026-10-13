import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";

type Status = "yes" | "no" | "partial";

const ROWS: { feature: string; skin: string; gpt: string; gptSt: Status; claude: string; claudeSt: Status }[] = [
  { feature:"사내 데이터 접근",   skin:"BigQuery 직결",    gpt:"없음",       gptSt:"no",      claude:"없음",      claudeSt:"no" },
  { feature:"CS 제품 정보",      skin:"739건 전용 DB",    gpt:"없음",       gptSt:"no",      claude:"없음",      claudeSt:"no" },
  { feature:"구글 워크스페이스",  skin:"Gmail·Calendar",  gpt:"없음",       gptSt:"no",      claude:"없음",      claudeSt:"no" },
  { feature:"노션 연동",         skin:"실시간 조회",       gpt:"없음",       gptSt:"no",      claude:"없음",      claudeSt:"no" },
  { feature:"데이터 보안",       skin:"사내망 격리",       gpt:"외부 전송",  gptSt:"partial", claude:"외부 전송", claudeSt:"partial" },
  { feature:"차트 자동 생성",    skin:"Chart.js 자동화",   gpt:"유료 플랜만",gptSt:"partial", claude:"없음",      claudeSt:"no" },
  { feature:"LLM 선택",         skin:"Gemini + Claude",  gpt:"GPT 계열만", gptSt:"partial", claude:"Claude만",  claudeSt:"partial" },
  { feature:"한국어 사내 용어",  skin:"직접 학습·적용",    gpt:"범용",       gptSt:"partial", claude:"범용",      claudeSt:"partial" },
];

function Pill({ text, st }: { text: string; st: Status }) {
  const col = st === "yes" ? C.orange : st === "no" ? "#333d4d" : "#3a4050";
  const textCol = st === "yes" ? C.orange : st === "no" ? C.gray600 : "#7a8595";
  return (
    <div style={{
      display:"inline-flex", alignItems:"center", justifyContent:"center",
      padding:"3px 10px", borderRadius:5,
      border:`1px solid ${col}`,
      fontSize:13, color: textCol, fontWeight: st === "yes" ? 600 : 400,
      background: st === "yes" ? `${C.orange}0d` : "transparent",
    }}>{text}</div>
  );
}

export const DifferentiatorsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const headOp = interpolate(frame,[0,18],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      boxSizing:"border-box", display:"flex", flexDirection:"column", padding:"40px 80px",
    }}>
      <div style={{ opacity: headOp, display:"flex", alignItems:"center", gap:16, marginBottom:24, flexShrink:0 }}>
        <div style={{ width:3, height:32, background: C.orange, borderRadius:2 }} />
        <div style={{ fontSize:32, fontWeight:800, color: C.white }}>왜 ChatGPT·Claude가 아닌가?</div>
      </div>

      <div style={{ flex:1, display:"flex", flexDirection:"column", border:`1px solid ${C.cardBorder}`, borderRadius:10, overflow:"hidden" }}>
        <div style={{
          display:"grid", gridTemplateColumns:"1.1fr 1.3fr 1fr 1fr",
          background: C.card, padding:"11px 20px",
          borderBottom:`1px solid ${C.cardBorder}`,
        }}>
          {["기능","SKIN1004 AI","ChatGPT","Claude.ai"].map((h,i) => (
            <div key={i} style={{ fontSize:12, fontWeight:600, color: i===1 ? C.orange : C.gray600, textTransform:"uppercase", letterSpacing:1 }}>{h}</div>
          ))}
        </div>

        {ROWS.map((row, i) => {
          const delay = 12 + i * 11;
          const op = interpolate(frame,[delay,delay+12],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
          return (
            <div key={i} style={{
              opacity: op,
              display:"grid", gridTemplateColumns:"1.1fr 1.3fr 1fr 1fr",
              padding:"11px 20px", alignItems:"center",
              background: i % 2 === 0 ? "transparent" : `${C.cardBorder}18`,
              borderBottom:`1px solid ${C.cardBorder}18`,
            }}>
              <div style={{ fontSize:14, color: C.white }}>{row.feature}</div>
              <div>
                <span style={{
                  fontSize:13, color: C.orange, fontWeight:600,
                  background:`${C.orange}0d`, padding:"3px 10px", borderRadius:5,
                  border:`1px solid ${C.orange}33`,
                }}>{row.skin}</span>
              </div>
              <Pill text={row.gpt} st={row.gptSt} />
              <Pill text={row.claude} st={row.claudeSt} />
            </div>
          );
        })}
      </div>

      {frame >= 140 && (
        <div style={{
          marginTop:16, flexShrink:0,
          opacity: interpolate(frame,[140,156],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"}),
          padding:"12px 20px",
          borderLeft:`3px solid ${C.orange}`,
          background:`${C.orange}08`,
          borderRadius:"0 8px 8px 0",
        }}>
          <div style={{ fontSize:14, color: C.white, fontWeight:600 }}>범용 AI는 사내 데이터에 접근할 수 없고, 데이터를 외부 서버로 전송합니다.</div>
          <div style={{ fontSize:12, color: C.gray600, marginTop:3 }}>SKIN1004 AI 에이전트는 사내망 안에서만 동작하며, 어떤 데이터도 외부로 나가지 않습니다.</div>
        </div>
      )}
    </div>
  );
};
