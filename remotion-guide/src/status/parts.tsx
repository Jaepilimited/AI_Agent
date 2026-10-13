import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";

export const ease = (frame: number, start: number, dur = 16) =>
  interpolate(frame, [start, start + dur], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

export const rise = (frame: number, start: number, dist = 20, dur = 18) =>
  interpolate(frame, [start, start + dur], [dist, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

/** 모든 발표 씬의 바깥 껍데기 — 배경·여백·제목을 한 곳에서 관리한다. */
export const Slide: React.FC<{
  kicker?: string;
  title: string;
  children: React.ReactNode;
}> = ({ kicker, title, children }) => {
  const f = useCurrentFrame();
  return (
    <div style={{
      width: "100%", height: "100%", background: BG, fontFamily: FONT,
      padding: "72px 96px", display: "flex", flexDirection: "column", gap: 8,
    }}>
      {kicker && (
        <div style={{
          opacity: ease(f, 0, 12),
          fontSize: 20, fontWeight: 700, color: C.orange, letterSpacing: 6,
        }}>{kicker}</div>
      )}
      <div style={{
        opacity: ease(f, 4, 14), transform: `translateY(${rise(f, 4, 14)}px)`,
        fontSize: 52, fontWeight: 800, color: C.white, letterSpacing: -0.5,
        marginBottom: 28,
      }}>{title}</div>
      {/* ⚠️ 본문을 세로로 **고르게 채운다.** flex-start 로 두면 1080 화면 아래
          절반이 비어 발표에서 허전하다 (실측 렌더로 확인). */}
      <div style={{
        flex: 1, display: "flex", flexDirection: "column",
        justifyContent: "center", gap: 4,
      }}>{children}</div>
    </div>
  );
};

/** 큰 숫자 하나 — 발표에서 가장 잘 읽히는 단위. */
export const Stat: React.FC<{
  value: string; label: string; note?: string;
  delay?: number; accent?: string; big?: boolean;
}> = ({ value, label, note, delay = 0, accent = C.orange, big }) => {
  const f = useCurrentFrame();
  return (
    <div style={{
      opacity: ease(f, delay), transform: `translateY(${rise(f, delay)}px)`,
      background: C.card, border: `1px solid ${C.cardBorder}`, borderRadius: 18,
      padding: big ? "38px 34px" : "30px 30px", flex: 1, minWidth: 0,
      display: "flex", flexDirection: "column", gap: 6,
    }}>
      <div style={{ fontSize: big ? 66 : 46, fontWeight: 800, color: accent, lineHeight: 1 }}>
        {value}
      </div>
      <div style={{ fontSize: big ? 22 : 19, fontWeight: 700, color: C.gray100 }}>{label}</div>
      {note && <div style={{ fontSize: 15, color: C.gray400, lineHeight: 1.45 }}>{note}</div>}
    </div>
  );
};

/** 전/후 비교 — 오답 수정을 보여주는 데 쓴다. */
export const BeforeAfter: React.FC<{
  title: string; before: string; after: string; why: string; delay?: number;
}> = ({ title, before, after, why, delay = 0 }) => {
  const f = useCurrentFrame();
  return (
    <div style={{
      opacity: ease(f, delay), transform: `translateY(${rise(f, delay)}px)`,
      background: C.card, border: `1px solid ${C.cardBorder}`, borderRadius: 16,
      padding: "26px 30px", display: "flex", alignItems: "center", gap: 24,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 21, fontWeight: 700, color: C.gray100, marginBottom: 6 }}>
          {title}
        </div>
        <div style={{ fontSize: 15, color: C.gray400 }}>{why}</div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexShrink: 0 }}>
        <span style={{
          fontSize: 24, fontWeight: 700, color: C.gray600,
          textDecoration: "line-through",
        }}>{before}</span>
        <span style={{ fontSize: 22, color: C.gray600 }}>→</span>
        <span style={{ fontSize: 30, fontWeight: 800, color: C.green }}>{after}</span>
      </div>
    </div>
  );
};

/** 가로 막대 — 경로별 분포처럼 비중이 중요한 데이터. */
export const Bar: React.FC<{
  label: string; n: number; max: number; color: string; delay?: number;
}> = ({ label, n, max, color, delay = 0 }) => {
  const f = useCurrentFrame();
  const w = interpolate(f, [delay, delay + 22], [0, (n / max) * 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, opacity: ease(f, delay) }}>
      <div style={{ width: 150, fontSize: 19, color: C.gray100, textAlign: "right" }}>{label}</div>
      <div style={{ flex: 1, height: 34, background: "#0e1420", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ width: `${w}%`, height: "100%", background: color, borderRadius: 8 }} />
      </div>
      <div style={{ width: 84, fontSize: 20, fontWeight: 700, color: C.gray100 }}>
        {n.toLocaleString()}
      </div>
    </div>
  );
};
