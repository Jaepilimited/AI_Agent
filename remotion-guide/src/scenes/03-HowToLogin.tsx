import React from "react";
import { interpolate, staticFile, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";

const STEPS = [
  { img:"screenshots/01_login_page.png",   title:"이름 입력",         desc:"2자 이상 입력하면 자동완성 목록 표시" },
  { img:"screenshots/02_name_typed.png",   title:"이름 선택",         desc:"클릭하면 소속 팀이 자동으로 채워짐" },
  { img:"screenshots/03_dept_selected.png",title:"비밀번호 입력 → 로그인", desc:"사내 AD 계정으로 바로 사용" },
];

export const HowToLoginScene: React.FC = () => {
  const frame = useCurrentFrame();

  const headOp = interpolate(frame, [0, 18], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const step = frame < 70 ? 0 : frame < 140 ? 1 : 2;

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      boxSizing:"border-box", display:"flex", flexDirection:"column", padding:"44px 72px",
    }}>
      {/* Header */}
      <div style={{ opacity: headOp, display:"flex", alignItems:"center", gap:16, marginBottom:32, flexShrink:0 }}>
        <div style={{ width:3, height:32, background: C.orange, borderRadius:2 }} />
        <div style={{ fontSize:32, fontWeight:800, color: C.white }}>로그인</div>
        <div style={{ marginLeft:"auto", fontSize:13, color: C.gray600, padding:"5px 14px", border:`1px solid ${C.cardBorder}`, borderRadius:6 }}>
          AD 계정 · 별도 가입 불필요
        </div>
      </div>

      <div style={{ flex:1, display:"flex", gap:40, overflow:"hidden" }}>
        {/* Steps */}
        <div style={{ width:280, flexShrink:0, display:"flex", flexDirection:"column", gap:12, justifyContent:"center" }}>
          {STEPS.map((s, i) => {
            const active = i === step;
            const done   = i < step;
            const op = interpolate(frame, [i * 70, i * 70 + 18], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
            return (
              <div key={i} style={{
                opacity: op,
                display:"flex", alignItems:"flex-start", gap:14,
                padding:"14px 16px", borderRadius:10,
                background: active ? `${C.orange}10` : "transparent",
                border:`1px solid ${active ? C.orange + "44" : "transparent"}`,
              }}>
                <div style={{
                  width:26, height:26, borderRadius:"50%", flexShrink:0,
                  background: done ? C.green : active ? C.orange : C.cardBorder,
                  display:"flex", alignItems:"center", justifyContent:"center",
                  fontSize:12, fontWeight:700, color: done || active ? C.dark : C.gray600,
                  marginTop:2,
                }}>{done ? "✓" : i + 1}</div>
                <div>
                  <div style={{ fontSize:16, fontWeight:700, color: active ? C.orange : done ? C.white : C.gray400 }}>{s.title}</div>
                  <div style={{ fontSize:13, color: C.gray600, marginTop:3, lineHeight:1.5 }}>{s.desc}</div>
                </div>
              </div>
            );
          })}

          {frame >= 150 && (
            <div style={{
              opacity: interpolate(frame,[150,166],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"}),
              marginTop:8, padding:"12px 16px",
              background: C.card, border:`1px solid ${C.cardBorder}`,
              borderRadius:10, borderLeft:`3px solid ${C.blue}`,
              fontSize:13, color: C.gray400, lineHeight:1.7,
            }}>
              사내 AD와 자동 동기화<br/>
              JWT 쿠키 인증 · 7일 유효
            </div>
          )}
        </div>

        {/* Screenshot */}
        <div style={{ flex:1, position:"relative", borderRadius:10, overflow:"hidden", border:`1px solid ${C.cardBorder}` }}>
          {STEPS.map((s, i) => {
            const start = i * 70;
            const op = interpolate(frame, [start, start + 20], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
            return (
              <div key={i} style={{ position:"absolute", inset:0, opacity: step === i ? op : step > i ? 0 : 0 }}>
                <img src={staticFile(s.img)} style={{ width:"100%", height:"100%", objectFit:"cover", objectPosition:"top center" }} />
                {step === i && (
                  <div style={{
                    position:"absolute", bottom:20, left:"50%", transform:"translateX(-50%)",
                    background:"rgba(14,20,32,0.88)", border:`1.5px solid ${C.orange}`,
                    borderRadius:24, padding:"8px 24px",
                    color: C.white, fontSize:16, fontWeight:700,
                    opacity: interpolate(frame, [start+20, start+34],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"}),
                    whiteSpace:"nowrap",
                  }}>{s.title}</div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
