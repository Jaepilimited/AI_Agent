import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { C, FONT } from "../tokens";

interface Props {
  text: string;
  delay?: number;
  color?: string;
  x?: number;
  y?: number;
  width?: number;
}

export const Callout: React.FC<Props> = ({
  text, delay = 0, color = C.orange,
  x = 50, y = 50, width = 360,
}) => {
  const frame = useCurrentFrame();
  const op = interpolate(frame, [delay, delay + 15], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const scale = interpolate(frame, [delay, delay + 12], [0.85, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <div style={{
      position: "absolute", left: x, top: y, width,
      opacity: op, transform: `scale(${scale})`,
      background: color, borderRadius: 12, padding: "12px 18px",
      color: C.white, fontFamily: FONT, fontSize: 22, fontWeight: 700,
      boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
      lineHeight: 1.4,
    }}>
      {text}
    </div>
  );
};

export const Arrow: React.FC<{
  x: number; y: number; dir?: "right" | "down" | "left"; delay?: number;
}> = ({ x, y, dir = "right", delay = 0 }) => {
  const frame = useCurrentFrame();
  const op = interpolate(frame, [delay, delay + 12], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const pulse = Math.sin((frame - delay) * 0.2) * 6;
  const rotate = dir === "down" ? 90 : dir === "left" ? 180 : 0;

  return (
    <div style={{
      position: "absolute", left: x + (dir === "right" ? pulse : 0),
      top: y + (dir === "down" ? pulse : 0),
      opacity: op,
      transform: `rotate(${rotate}deg)`,
      fontSize: 48, color: C.orange,
      textShadow: "0 2px 8px rgba(0,0,0,0.5)",
    }}>
      →
    </div>
  );
};
