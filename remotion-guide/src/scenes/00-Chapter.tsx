import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";

interface Props {
  act: string;   // e.g. "ACT 1"
  title: string; // e.g. "사용 방법"
}

export const ChapterScene: React.FC<Props> = ({ act, title }) => {
  const frame = useCurrentFrame();

  const lineW = interpolate(frame, [8, 38], [0, 260], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const textOp = interpolate(frame, [20, 38], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const titleOp = interpolate(frame, [32, 50], [0, 1], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });
  const titleY = interpolate(frame, [32, 50], [12, 0], { extrapolateLeft:"clamp", extrapolateRight:"clamp" });

  return (
    <div style={{
      width:"100%", height:"100%", background: BG, fontFamily: FONT,
      display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center",
      gap:20,
    }}>
      {/* Top line */}
      <div style={{ width: lineW, height:1.5, background: C.orange, borderRadius:1 }} />

      {/* ACT label */}
      <div style={{
        opacity: textOp,
        fontSize:18, fontWeight:600, color: C.orange,
        letterSpacing:8, textTransform:"uppercase",
      }}>{act}</div>

      {/* Bottom line */}
      <div style={{ width: lineW, height:1.5, background: C.orange, borderRadius:1 }} />

      {/* Chapter title */}
      {title && (
        <div style={{
          opacity: titleOp,
          transform:`translateY(${titleY}px)`,
          marginTop:8,
          fontSize:28, fontWeight:700, color: C.gray400,
          letterSpacing:3,
        }}>{title}</div>
      )}
    </div>
  );
};
