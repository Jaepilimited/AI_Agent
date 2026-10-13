import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT } from "../tokens";

interface Props {
  src: string;
  label?: string;
  delay?: number;
  zoom?: number;
  zoomX?: number;
  zoomY?: number;
}

export const ScreenFrame: React.FC<Props> = ({
  src, label, delay = 0, zoom = 1, zoomX = 50, zoomY = 50,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const appear = spring({ frame: frame - delay, fps, config: { damping: 16, stiffness: 140 } });
  const op = interpolate(frame, [delay, delay + 20], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const currentZoom = interpolate(frame, [delay, delay + 60], [1, zoom], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <div style={{
      width: "100%", height: "100%",
      position: "relative", overflow: "hidden",
      opacity: op,
    }}>
      {/* Screen shadow border */}
      <div style={{
        position: "absolute", inset: 0,
        border: `3px solid ${C.cardBorder}`,
        borderRadius: 8,
        boxShadow: "0 8px 40px rgba(0,0,0,0.6)",
        zIndex: 2,
        pointerEvents: "none",
      }} />

      {/* Screenshot image */}
      <img
        src={src}
        style={{
          width: "100%", height: "100%",
          objectFit: "cover",
          objectPosition: `${zoomX}% ${zoomY}%`,
          transform: `scale(${currentZoom})`,
          transformOrigin: `${zoomX}% ${zoomY}%`,
          display: "block",
        }}
      />

      {/* Label bar */}
      {label && (
        <div style={{
          position: "absolute", bottom: 0, left: 0, right: 0,
          background: "linear-gradient(transparent, rgba(0,0,0,0.8))",
          padding: "32px 24px 16px",
          color: C.white, fontFamily: FONT, fontSize: 20,
          opacity: appear,
          transform: `translateY(${(1 - appear) * 20}px)`,
          zIndex: 3,
        }}>
          {label}
        </div>
      )}
    </div>
  );
};
