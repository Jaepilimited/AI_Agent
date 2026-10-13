import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";

const ITEMS = [
  { num:"1", col:C.orange, title:"SQL 몰라도 됩니다",       body:"자연어로 질문하면 SQL 자동 생성·실행·분석 한 번에. BigQuery 권한 없어도 즉시 조회.", tags:["자연어","BigQuery","차트 자동생성"] },
  { num:"2", col:C.blue,   title:"외부로 나가는 데이터 없음", body:"사내망(172.16.x.x) 격리 서버. ChatGPT·Claude.ai와 달리 데이터가 외부 서버로 전송되지 않음.", tags:["사내망 격리","데이터 보안"] },
  { num:"3", col:C.purple, title:"SKIN1004 전용 지식",       body:"제품 CS 739건, 큐텐 메가와리 일정, 노션 내부 문서 즉시 참조. 범용 AI는 이 데이터를 모름.", tags:["CS DB","노션","메가와리"] },
  { num:"4", col:C.green,  title:"설치 없이, 교육 없이",     body:"브라우저에서 172.16.1.250:3000 접속. AD 계정으로 로그인. 별도 프로그램·API 설정 불필요.", tags:["브라우저","AD 계정","즉시 사용"] },
];

export const AdvantagesScene: React.FC = () => {
  const frame = useCurrentFrame();
  const headOp = interpolate(frame,[0,18],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      boxSizing:"border-box", display:"flex", flexDirection:"column", padding:"40px 80px",
    }}>
      <div style={{ opacity: headOp, display:"flex", alignItems:"center", gap:16, marginBottom:28, flexShrink:0 }}>
        <div style={{ width:3, height:32, background: C.orange, borderRadius:2 }} />
        <div style={{ fontSize:32, fontWeight:800, color: C.white }}>도입하면 무엇이 달라지나요?</div>
      </div>

      <div style={{ display:"grid", gridTemplateColumns:"repeat(2, 1fr)", gap:20, flex:1 }}>
        {ITEMS.map((card, i) => {
          const delay = 16 + i * 22;
          const op = interpolate(frame,[delay,delay+18],[0,1],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
          const y  = interpolate(frame,[delay,delay+18],[18,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
          return (
            <div key={i} style={{
              opacity: op, transform:`translateY(${y}px)`,
              background: C.card, border:`1px solid ${C.cardBorder}`,
              borderRadius:10, padding:"22px 24px",
              display:"flex", flexDirection:"column", gap:10,
              borderTop:`2px solid ${card.col}`,
            }}>
              <div style={{ display:"flex", alignItems:"center", gap:12 }}>
                <div style={{
                  width:26, height:26, borderRadius:"50%", flexShrink:0,
                  background:`${card.col}22`, border:`1px solid ${card.col}66`,
                  display:"flex", alignItems:"center", justifyContent:"center",
                  fontSize:13, fontWeight:700, color: card.col,
                }}>{card.num}</div>
                <div style={{ fontSize:19, fontWeight:800, color: C.white }}>{card.title}</div>
              </div>
              <div style={{ fontSize:14, color: C.gray400, lineHeight:1.7, flex:1 }}>{card.body}</div>
              <div style={{ display:"flex", flexWrap:"wrap", gap:6 }}>
                {card.tags.map((t, j) => (
                  <div key={j} style={{
                    fontSize:11, color: card.col,
                    padding:"2px 9px", borderRadius:4,
                    border:`1px solid ${card.col}33`,
                    background:`${card.col}0a`,
                  }}>{t}</div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
