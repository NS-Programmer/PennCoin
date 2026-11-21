# UI style guide for Ninoo Builder — "Filings Desk"

Paste this into Ninoo Builder in place of the "Look and feel" section of `NINOO_PROMPT.md`.

---

## Style direction
Design the app in a **restrained, institutional, editorial style** — the feel of a premium investment bank's client portal: calm, confident, lots of white space, very little decoration, numbers and decisions given room to breathe. It must look like **its own brand**, not any real bank: no real company names, logos, wordmarks, proprietary fonts or copied page layouts.

Brand name shown in the header: **Filings Desk** · small subtitle **"eUSD-sim compliance (SIMULATED)"**. No logo image; the wordmark is the name set in the heading font.

## Principles
1. **Quiet by default, loud only for risk.** Most of the screen is neutral; color appears only for status, deadlines and alerts.
2. **Editorial hierarchy.** One clear headline per screen, a one-line summary under it, then content. No more than three type sizes on a screen.
3. **Numbers first.** Key figures (cases today, filings due, dollar values, due dates) are large, tabular and aligned.
4. **Generous spacing.** Prefer white space and thin rules over boxes, shadows and backgrounds.
5. **Plain, precise language.** Sentence case everywhere. Short labels. No exclamation marks, no emoji.

## Color palette (our imaginary brand)
| Role | Name | Hex | Use |
|---|---|---|---|
| Primary | Evergreen | `#0F3D3E` | Header bar, primary buttons, active tab underline, links |
| Primary hover | Evergreen dark | `#0A2C2D` | Hover/pressed for primary |
| Accent | Brass | `#B08D57` | Thin accent rules, selected-row marker, the "next deadline" highlight. Never for body text (low contrast) |
| Background | Paper | `#FAFAF7` | Page background |
| Surface | White | `#FFFFFF` | Cards, tables, forms |
| Rule | Stone | `#E4E2DC` | 1px dividers and table lines |
| Text | Ink | `#1B1F23` | Body text and figures |
| Text secondary | Slate | `#5B6470` | Labels, captions, timestamps |
| Success | Pine | `#276B4E` | "passed", "filed" |
| Warning | Amber | `#946210` | Volume alerts, due within 3 business days |
| Danger | Brick | `#B42318` | Validator problems, refusals, overdue |
| Info | Harbor | `#2B6CB0` | "approved", neutral information |
| Simulated badge | Sand | `#F3EBDD` fill, `#7A5C2E` text | The "SIMULATED DATA" badge |

Status chips use a 10% tint of the status color as fill and the full color for text (e.g., Pine `#276B4E` on `#EAF3EE`, Amber `#946210` on `#FBF3E6`, Brick on `#FBEAE8`, Harbor on `#EAF1F8`). All text must meet WCAG AA contrast (4.5:1 for body text).

## Typography (open-license fonts)
| Use | Font | Size / weight |
|---|---|---|
| Page headline | Source Serif 4 | 32px / 600 |
| Section heading | Source Serif 4 | 22px / 600 |
| Body and UI | Inter | 15px / 400, line height 1.55 |
| Labels and captions | Inter | 13px / 500, Slate, letter-spacing 0.02em |
| Key figures | Inter, tabular numbers (`font-variant-numeric: tabular-nums`) | 36px / 600 |
| Wallet addresses and tx hashes | IBM Plex Mono | 13px / 400, shortened `0x098B…6E2f96` with a copy button |

## Layout
- 12-column grid, max content width 1200px, 32px outer margin, 24px gutters.
- 8px spacing scale (8, 16, 24, 32, 48, 64).
- **Header:** full-width Evergreen bar, 64px tall: wordmark left (white serif), navigation center (white, 15px; active item has a 2px Brass underline), "SIMULATED DATA" badge and the simulated date right.
- **Page header:** headline + one-line summary + right-aligned actions (e.g., "Next day", "Reset demo").
- Sections separated by 48px of space and a 1px Stone rule, not by cards with shadows.
- Corners: 4px on buttons, inputs and chips; 6px on cards. No drop shadows except a subtle one on open menus and dialogs.

## Components
- **Buttons:** Primary = Evergreen fill, white text. Secondary = white fill, 1px Evergreen border, Evergreen text. Destructive (Reject) = white fill, Brick border and text. Height 40px, 16px horizontal padding, Inter 14px/600.
- **Number tiles (briefing):** no box; a big tabular figure, a 13px Slate label above it and a 2px Brass rule on top of the most important tile ("Need a filing").
- **Tables (review queue, audit):** white surface, 48px rows, 1px Stone row lines, no vertical lines, Slate header text in 13px caps-free labels, right-aligned numbers and dates, hover row in `#F4F3EF`. Selected row: 3px Brass bar on the left edge.
- **Status chips:** pill, 24px tall, tinted fill as above. Text: awaiting review, approved, filed, rejected, needs fix.
- **Due dates:** plain Ink normally; Amber text when ≤3 business days away; Brick text plus "overdue" chip when past due.
- **"Why this filing" box (review item):** Paper background, 3px Evergreen left rule, Source Serif 4 18px for the explanation, Inter 13px Slate for the rule citation.
- **Validator problems:** Brick left rule, list of problems in Ink with a Brick icon, shown next to the field they refer to when possible.
- **Forms:** labels above fields, 40px inputs, 1px Stone border turning Evergreen on focus, error text in Brick below the field.
- **Assistant chat:** messages in plain text without bubbles; the assistant's replies on Paper with a thin Evergreen left rule; a small "Guardrail: unfreeze" tag in Brick tint when a guardrail fired.
- **Empty states:** one serif line ("Nothing due today.") and one Slate sentence of help. No illustrations.

## Charts (scorecard)
Flat and minimal: Evergreen for the main series, Brass for comparison, Stone gridlines, direct labels instead of legends, no 3D, no gradients.

## Copy tone
Formal, short and exact: "Blocked property report due Feb 16, 2027." Not "Heads up! Your report is due soon!" Use the API's error messages exactly as returned.

## Do not
- Use any real bank's name, logo, wordmark, proprietary typeface or recognizable page layout.
- Use gradients, glassmorphism, heavy shadows, stock photos or illustrations.
- Use Brass for text or for anything that must be read.
- Hide the "SIMULATED DATA" badge on any screen.
