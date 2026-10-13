import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { BG, C, FONT } from "../tokens";
import { AS_OF, AUTOMATION, FEEDBACK, FIXES, GUARDS, NEXT, ROUTES, USAGE } from "./data";
import { Bar, BeforeAfter, Slide, Stat, ease, rise } from "./parts";

/* ── 01. 표지 ───────────────────────────────────────────── */
export const Cover: React.FC = () => {
  const f = useCurrentFrame();
  const line = interpolate(f, [10, 40], [0, 300], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      width: "100%", height: "100%", background: BG, fontFamily: FONT,
      display: "flex", flexDirection: "column", alignItems: "center",
      justifyContent: "center", gap: 18,
    }}>
      <div style={{
        opacity: ease(f, 6), fontSize: 22, fontWeight: 700,
        color: C.orange, letterSpacing: 10,
      }}>CELLA</div>
      <div style={{ width: line, height: 2, background: C.orange, borderRadius: 1 }} />
      <div style={{
        opacity: ease(f, 18), transform: `translateY(${rise(f, 18)}px)`,
        fontSize: 68, fontWeight: 800, color: C.white, letterSpacing: -1,
      }}>운영 현황 보고</div>
      <div style={{
        opacity: ease(f, 30), fontSize: 25, color: C.gray400, marginTop: 4,
      }}>사내 AI 어시스턴트 — 사용·품질·개선</div>
      <div style={{
        opacity: ease(f, 42), fontSize: 17, color: C.gray600, marginTop: 22,
        letterSpacing: 2,
      }}>{AS_OF}  |  데이터분석파트</div>
    </div>
  );
};

/* ── 02. 사용 현황 ──────────────────────────────────────── */
export const Usage: React.FC = () => (
  <Slide kicker="01  사용 현황" title="쓰이고 있습니다">
    <div style={{ display: "flex", gap: 18, marginBottom: 18 }}>
      <Stat big value={USAGE.questionsTotal.toLocaleString()} label="누적 질문"
            note={`대화 ${USAGE.conversations.toLocaleString()}개 · 메시지 ${USAGE.messages.toLocaleString()}개`} delay={10} />
      <Stat big value={`${USAGE.questionsAug}건`} label="8월 질문"
            note={`사용자 ${USAGE.usersAug}명`} delay={18} accent={C.blue} />
      <Stat big value={`${USAGE.avgSeconds}초`} label="평균 응답"
            note="조회·문서 검색 포함" delay={26} accent={C.green} />
    </div>
    <div style={{
      opacity: ease(useCurrentFrame(), 36),
      background: C.card, border: `1px solid ${C.cardBorder}`,
      borderRadius: 16, padding: "22px 26px",
    }}>
      <div style={{ fontSize: 20, color: C.gray100, lineHeight: 1.6 }}>
        다만 <b style={{ color: C.orange }}>AD 계정 {USAGE.adAccounts}명 중 가입 {USAGE.signedUp}명</b>,
        8월에 실제로 쓴 사람은 <b style={{ color: C.orange }}>{USAGE.usersAug}명</b>입니다.
        <span style={{ color: C.gray400 }}> — 성능이 아니라 확산이 다음 과제입니다.</span>
      </div>
    </div>
  </Slide>
);

/* ── 03. 무엇을 묻나 ────────────────────────────────────── */
export const What: React.FC = () => {
  const max = Math.max(...ROUTES.map((r) => r.n));
  return (
    <Slide kicker="02  무엇을 묻나" title="8월 질문 629건의 구성">
      <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 4 }}>
        {ROUTES.map((r, i) => (
          <Bar key={r.key} label={r.label} n={r.n} max={max} color={r.color} delay={12 + i * 7} />
        ))}
      </div>
      <div style={{
        opacity: ease(useCurrentFrame(), 62), marginTop: 26,
        fontSize: 19, color: C.gray400, lineHeight: 1.6,
      }}>
        절반 이상이 <b style={{ color: C.orange }}>데이터 조회</b>입니다.
        숫자가 틀리면 바로 업무에 영향이 간다는 뜻이라, 아래 개선은 전부 여기에 집중했습니다.
      </div>
    </Slide>
  );
};

/* ── 04. 무엇을 고쳤나 ──────────────────────────────────── */
export const Fixes: React.FC = () => (
  <Slide kicker="03  품질" title="이번에 고친 오답">
    <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
      {FIXES.map((x, i) => (
        <BeforeAfter key={x.title} {...x} delay={12 + i * 12} />
      ))}
    </div>
    <div style={{
      opacity: ease(useCurrentFrame(), 72), marginTop: 20,
      fontSize: 19, color: C.gray400, lineHeight: 1.6,
    }}>
      공통점은 <b style={{ color: C.red }}>에러가 나지 않았다</b>는 것입니다.
      숫자가 그럴듯하게 작게 나왔을 뿐이라, 제보가 없었으면 계속 틀린 채로 답했습니다.
    </div>
  </Slide>
);

/* ── 05. 피드백 ─────────────────────────────────────────── */
export const Feedback: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Slide kicker="04  피드백" title="제보는 있었지만, 답이 없었습니다">
      <div style={{ display: "flex", gap: 18, marginBottom: 20 }}>
        <Stat value={`${FEEDBACK.totalDown}건`} label="누적 피드백"
              note={`코멘트가 달린 것 ${FEEDBACK.withComment}건`} delay={10} />
        <Stat value={`${FEEDBACK.repliesBefore}건`} label="제보자가 받은 답"
              note="읽는 화면 자체가 없었습니다" delay={18} accent={C.red} />
        <Stat value={`${FEEDBACK.handled}건`} label="지금 처리 완료"
              note={`회신 대기 ${FEEDBACK.repliesPending}건`} delay={26} accent={C.green} />
      </div>
      <div style={{
        opacity: ease(f, 38),
        background: C.card, border: `1px solid ${C.cardBorder}`,
        borderRadius: 16, padding: "24px 28px", lineHeight: 1.7,
      }}>
        <div style={{ fontSize: 20, color: C.gray100 }}>
          한 분은 <b style={{ color: C.orange }}>같은 원인을 네 번</b> 신고했습니다.
          답이 돌아가지 않으니 같은 것을 계속 겪으며 계속 신고한 것입니다.
        </div>
        <div style={{ fontSize: 18, color: C.gray400, marginTop: 10 }}>
          → 처리함·처리 상태·<b style={{ color: C.green }}>회신 경로</b>를 만들었습니다.
          이제 반영 결과가 제보자 화면에 표시됩니다.
        </div>
      </div>
    </Slide>
  );
};

/* ── 06. 신뢰 장치 ──────────────────────────────────────── */
export const Guards: React.FC = () => (
  <Slide kicker="05  신뢰" title="틀리면 티가 나게 만들었습니다">
    <div style={{ display: "flex", gap: 16, marginBottom: 22 }}>
      {GUARDS.map((g, i) => (
        <Stat key={g.label} value={g.value} label={g.label} note={g.note}
              delay={10 + i * 8} accent={i % 2 ? C.blue : C.orange} />
      ))}
    </div>
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {AUTOMATION.map((a, i) => {
        const f = useCurrentFrame();
        return (
          <div key={a.label} style={{
            opacity: ease(f, 46 + i * 7), display: "flex", alignItems: "center", gap: 18,
            background: C.card, border: `1px solid ${C.cardBorder}`,
            borderRadius: 12, padding: "14px 22px",
          }}>
            <span style={{
              fontSize: 19, fontWeight: 700, color: C.orange,
              fontFamily: "Consolas, monospace", width: 66,
            }}>{a.time}</span>
            <span style={{ fontSize: 20, fontWeight: 700, color: C.gray100, width: 230 }}>
              {a.label}
            </span>
            <span style={{ fontSize: 17, color: C.gray400 }}>{a.detail}</span>
          </div>
        );
      })}
    </div>
  </Slide>
);

/* ── 07. 다음 ───────────────────────────────────────────── */
export const Next: React.FC = () => (
  <Slide kicker="06  다음" title="여기서부터입니다">
    <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 8 }}>
      {NEXT.map((t, i) => {
        const f = useCurrentFrame();
        return (
          <div key={t} style={{
            opacity: ease(f, 12 + i * 14), transform: `translateY(${rise(f, 12 + i * 14)}px)`,
            display: "flex", gap: 20, alignItems: "flex-start",
            background: C.card, border: `1px solid ${C.cardBorder}`,
            borderRadius: 16, padding: "26px 30px",
          }}>
            <span style={{
              fontSize: 30, fontWeight: 800, color: C.orange, lineHeight: 1, width: 40,
            }}>{i + 1}</span>
            <span style={{ fontSize: 23, color: C.gray100, lineHeight: 1.55 }}>{t}</span>
          </div>
        );
      })}
    </div>
  </Slide>
);

/* ── 08. 마무리 ─────────────────────────────────────────── */
export const Outro: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <div style={{
      width: "100%", height: "100%", background: BG, fontFamily: FONT,
      display: "flex", flexDirection: "column", alignItems: "center",
      justifyContent: "center", gap: 20,
    }}>
      <div style={{
        opacity: ease(f, 6), transform: `translateY(${rise(f, 6)}px)`,
        fontSize: 46, fontWeight: 800, color: C.white,
      }}>쓰면서 알려주시면 고쳐집니다</div>
      <div style={{
        opacity: ease(f, 20), fontSize: 22, color: C.gray400, lineHeight: 1.7,
        textAlign: "center", maxWidth: 900,
      }}>
        틀린 답을 보시면 <b style={{ color: C.orange }}>👎 버튼과 한 줄</b>이면 됩니다.<br />
        이번에 고친 것 대부분이 그렇게 들어온 제보였습니다.
      </div>
      <div style={{
        opacity: ease(f, 34), marginTop: 26,
        fontSize: 26, fontWeight: 700, color: C.orange,
        fontFamily: "Consolas, monospace",
      }}>http://10.1.100.5</div>
    </div>
  );
};
