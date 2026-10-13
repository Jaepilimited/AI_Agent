export const C = {
  orange:      "#e89200",
  orangeLight: "#f5a623",
  orangeDim:   "#e8920033",
  dark:        "#0e1420",
  card:        "#182232",
  cardBorder:  "#243040",
  white:       "#ffffff",
  gray100:     "#e8e8e8",
  gray400:     "#8a9ab0",
  gray600:     "#455060",
  green:       "#34d399",
  blue:        "#60a5fa",
  purple:      "#a78bfa",
  red:         "#f87171",
} as const;

// Cinematic warm-center background — use on every scene root div
export const BG = `radial-gradient(ellipse at 50% 72%, #2a1a0c 0%, #0e1420 52%)`;

export const FONT = "'Malgun Gothic', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif";
export const FONT_MONO = "'Consolas', 'Courier New', monospace";

export const W = 1920;
export const H = 1080;
export const FPS = 30;

export function fade(frame: number, start: number, dur = 16) {
  const { interpolate } = require("remotion");
  return interpolate(frame, [start, start + dur], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
}

export function slideUp(frame: number, start: number, dist = 24, dur = 18) {
  const { interpolate } = require("remotion");
  return interpolate(frame, [start, start + dur], [dist, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
}
