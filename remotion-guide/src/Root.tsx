import React from "react";
import { Composition, Series } from "remotion";
import { IntroScene }           from "./scenes/01-Intro";
import { ProblemScene }         from "./scenes/02-Problem";
import { HowToLoginScene }      from "./scenes/03-HowToLogin";
import { HowToDataScene }       from "./scenes/04-HowToData";
import { HowToCSScene }         from "./scenes/05-HowToCS";
import { DifferentiatorsScene } from "./scenes/06-Differentiators";
import { ArchitectureScene }    from "./scenes/07-Architecture";
import { AdvantagesScene }      from "./scenes/08-Advantages";
import { OutroScene }           from "./scenes/09-Outro";
import { ChapterScene }         from "./scenes/00-Chapter";
import { FPS, W, H } from "./tokens";
import { Cover, Feedback, Fixes, Guards, Next, Outro, Usage, What } from "./status/scenes";

// ─── Durations (frames @ 30fps) ────────────────────────────
// 01 Intro              90   (3s)
// ACT 1 chapter         60   (2s)
// 02 Problem           210   (7s)
// ACT 2 chapter         60   (2s)
// 03 Login             240   (8s)
// 04 DataQuery         360  (12s)
// 05 CS Demo           210   (7s)
// ACT 3 chapter         60   (2s)
// 06 Differentiators   240   (8s)
// ACT 4 chapter         60   (2s)
// 07 Architecture      270   (9s)
// 08 Advantages        210   (7s)
// 09 Outro             150   (5s)
// Total: 2220 frames  ~74s
// ────────────────────────────────────────────────────────────

const TOTAL_FRAMES =
  90 + 60 + 210 + 60 + 240 + 360 + 210 + 60 + 240 + 60 + 270 + 210 + 150;

const UserGuide: React.FC = () => (
  <Series>
    <Series.Sequence durationInFrames={90}>
      <IntroScene />
    </Series.Sequence>

    <Series.Sequence durationInFrames={60}>
      <ChapterScene act="ACT 1" title="사용 방법" />
    </Series.Sequence>

    <Series.Sequence durationInFrames={210}>
      <ProblemScene />
    </Series.Sequence>

    <Series.Sequence durationInFrames={60}>
      <ChapterScene act="ACT 2" title="기능 안내" />
    </Series.Sequence>

    <Series.Sequence durationInFrames={240}>
      <HowToLoginScene />
    </Series.Sequence>
    <Series.Sequence durationInFrames={360}>
      <HowToDataScene />
    </Series.Sequence>
    <Series.Sequence durationInFrames={210}>
      <HowToCSScene />
    </Series.Sequence>

    <Series.Sequence durationInFrames={60}>
      <ChapterScene act="ACT 3" title="차별화" />
    </Series.Sequence>

    <Series.Sequence durationInFrames={240}>
      <DifferentiatorsScene />
    </Series.Sequence>

    <Series.Sequence durationInFrames={60}>
      <ChapterScene act="ACT 4" title="아키텍처 & 장점" />
    </Series.Sequence>

    <Series.Sequence durationInFrames={270}>
      <ArchitectureScene />
    </Series.Sequence>
    <Series.Sequence durationInFrames={210}>
      <AdvantagesScene />
    </Series.Sequence>
    <Series.Sequence durationInFrames={150}>
      <OutroScene />
    </Series.Sequence>
  </Series>
);


// ─── Cella 운영 현황 보고 (2026-08-18) ──────────────────────
// 표지 5s · 사용 8s · 무엇을 묻나 9s · 오답 12s · 피드백 10s
// 신뢰 11s · 다음 8s · 마무리 6s  =  69s
const STATUS_SEQ = [
  { c: Cover,    f: 150 },
  { c: Usage,    f: 240 },
  { c: What,     f: 270 },
  { c: Fixes,    f: 360 },
  { c: Feedback, f: 300 },
  { c: Guards,   f: 330 },
  { c: Next,     f: 240 },
  { c: Outro,    f: 180 },
];
const STATUS_TOTAL = STATUS_SEQ.reduce((a, s) => a + s.f, 0);

const StatusReport: React.FC = () => (
  <Series>
    {STATUS_SEQ.map(({ c: Scene, f }, i) => (
      <Series.Sequence key={i} durationInFrames={f}>
        <Scene />
      </Series.Sequence>
    ))}
  </Series>
);

export const Root: React.FC = () => (
  <>
  <Composition
    id="UserGuide"
    component={UserGuide}
    durationInFrames={TOTAL_FRAMES}
    fps={FPS}
    width={W}
    height={H}
  />
    <Composition
      id="StatusReport"
      component={StatusReport}
      durationInFrames={STATUS_TOTAL}
      fps={FPS}
      width={W}
      height={H}
    />
  </>
);
