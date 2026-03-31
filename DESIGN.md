# Design System — SKIN1004 AI

## Product Context
- **What this is:** Enterprise AI chatbot for SKIN1004 employees — sales data queries, document search, CS answers, marketing analysis
- **Who it's for:** ~328 internal employees (marketing, sales, CS, management)
- **Space/industry:** K-beauty / cosmetics, enterprise internal tool
- **Project type:** Web app (sidebar + chat SPA)

## Aesthetic Direction
- **Direction:** Industrial/Warm — data tool functionality + K-beauty brand warmth
- **Decoration level:** Intentional — subtle surface treatments, warm neutrals, orange accent glow
- **Mood:** Professional but approachable. Bloomberg Terminal that works at a cosmetics company. Data is clear, interactions feel warm.
- **Reference sites:** ChatGPT (layout standard), Claude (warm tone), Gemini (generative UI)

## Typography
- **Display/Hero:** Figtree 700-900 — rounded terminals, warm geometric sans-serif. Friendly but authoritative for headings.
- **Body/UI:** DM Sans 400-600 — clean, slightly rounded, excellent readability. Pairs naturally with Korean text.
- **Data/Tables:** Geist Mono 400-600 — tabular-nums for perfect number alignment. Revenue, quantities, percentages all align column-perfect.
- **Code:** JetBrains Mono 400-500 — industry standard for SQL/code blocks.
- **Korean:** Pretendard — modern Korean sans-serif, replaces Apple SD Gothic/Malgun Gothic.
- **Loading:** Google Fonts CDN for Figtree, DM Sans, Geist Mono, JetBrains Mono. Pretendard via CDN or self-hosted.
- **Scale:**
  - 3xl: 2.5rem (40px) — hero brand
  - 2xl: 1.75rem (28px) — page titles
  - xl: 1.5rem (24px) — section headings
  - lg: 1.125rem (18px) — sub-headings
  - md: 1rem (16px) — body text
  - sm: 0.875rem (14px) — UI labels, secondary text
  - xs: 0.75rem (12px) — captions, metadata
  - 2xs: 0.65rem (10px) — badges, timestamps

## Color
- **Approach:** Restrained — orange accent is rare and meaningful, neutrals do the heavy lifting
- **Primary/Accent:** #e89200 — SKIN1004 brand orange, used for CTAs, active states, user message tint
- **Accent Hover:** #f0a020
- **Accent Dim:** rgba(232, 146, 0, 0.15) — backgrounds, subtle highlights

### Dark Theme (default)
- Background: #111111
- Surface: #1a1a1a
- Elevated: #222222
- Input: rgba(255,255,255,0.05)
- Hover: rgba(255,255,255,0.07)
- User message bg: rgba(232, 146, 0, 0.10)
- AI message bg: rgba(255,255,255,0.03)
- Border: rgba(255,255,255,0.08)
- Border strong: rgba(255,255,255,0.14)
- Text: #ebebeb
- Text secondary: rgba(255,255,255,0.55)
- Text muted: rgba(255,255,255,0.30)

### Light Theme
- Background: #fafaf9 (warm white, not pure white)
- Surface: #ffffff
- Elevated: #f5f4f2
- Input: rgba(0,0,0,0.04)
- Hover: rgba(0,0,0,0.05)
- User message bg: rgba(232, 146, 0, 0.07)
- AI message bg: rgba(0,0,0,0.02)
- Border: rgba(0,0,0,0.08)
- Border strong: rgba(0,0,0,0.14)
- Text: #1a1a1a
- Text secondary: rgba(0,0,0,0.55)
- Text muted: rgba(0,0,0,0.30)

### Semantic Colors
- Success: #22c55e
- Warning: #f59e0b
- Error: #ef4444
- Info: #3b82f6

## Spacing
- **Base unit:** 4px
- **Density:** Comfortable
- **Scale:**
  - 2xs: 2px
  - xs: 4px
  - sm: 8px
  - md: 16px
  - lg: 24px
  - xl: 32px
  - 2xl: 48px
  - 3xl: 64px

## Layout
- **Approach:** Sidebar + chat (ChatGPT standard, maintained)
- **Sidebar:** 260px collapsed, conversation list grouped by date
- **Chat area:** Flexible width, max message width 85%
- **Max content width:** 1200px (for dashboard/admin pages)
- **Border radius:**
  - sm: 6px — buttons, inputs, small elements
  - md: 12px — cards, alerts, code blocks
  - lg: 16px — message bubbles, modals
  - xl: 24px — main containers, chat mockup
  - full: 9999px — pills, badges, chips

## Motion
- **Approach:** Minimal-functional
- **Easing:** cubic-bezier(0.16, 1, 0.3, 1) — smooth overshoot for enters
- **Duration:**
  - Short: 150ms — hover states, color transitions
  - Medium: 250ms — theme toggle, surface transitions
  - Long: 400ms — message entrance (slide-up + fade-in)
- **Craver marquee:** Preserved on login page (brand identity animation)
- **Message animation:** slide-up 8px + fade-in on new messages

## Key UI Elements (preserve)
- **AI avatar:** C. logo (splash-dark-new.png / splash.png) next to AI responses
- **User avatar:** User name/initials next to user messages
- **Follow-up chips:** LLM-generated suggestion chips below AI responses
- **Code copy button:** Hover-reveal copy button on code blocks
- **Table/chart copy:** Individual copy buttons for tables and charts
- **Model selector:** Topbar dropdown (Gemini / Claude)
- **Sidebar:** Logo + brand, search, date-grouped conversations, Dashboard/Status/Admin buttons
- **Login:** Craver marquee background, glassmorphism card, department → name → password flow

## Currency Display
- No currency symbols (no ₩, no $) in data tables by default
- Use plain numbers with comma separators: 1,234,567,890
- Context-specific units added in column headers or labels when needed (e.g., "(KRW)" in header)

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-31 | Initial design system created | Competitive research (ChatGPT/Claude/Gemini) + product context analysis |
| 2026-03-31 | Figtree + DM Sans chosen over Outfit + Plus Jakarta Sans | User feedback: original fonts too rigid/stiff. Figtree's rounded terminals and DM Sans's soft curves feel warmer |
| 2026-03-31 | Montserrat replaced | Overused free font, gives template/amateur feel in commercial products |
| 2026-03-31 | Warm gray neutrals over cool gray | K-beauty brand warmth, pairs better with orange accent |
| 2026-03-31 | Currency symbols removed from default data display | User preference for cleaner number presentation |
