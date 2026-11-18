# Portal UI (single page)

References for tone/content: Illinois Institution Profile, Illinois Experts.

## Sections (top → bottom)
1. **Header/nav** — logo, anchor links to sections, "Ask the assistant" button, theme toggle.
2. **Hero** — UIUC name, campus image (`institution.images[0]`, role `hero`, a 1920x678 panorama; show its `attribution` line and link `source_url`), location (Urbana–Champaign, IL) with small Leaflet/OSM map, short intro, "Data as of 2026-11-16".
3. **KPI tiles** — works (2y), total citations, faculty in directory, % open access, median FWCI.
4. **Research focus** — treemap field → subfield; rising topics list (growth vs previous period).
5. **Scholarly output** — works per month (line/area), top venues, SDG distribution.
6. **Notable publications** — tabs *Most cited* / *Most recent*; search + field filter; card: title, date, venue, UIUC authors, DOI/OA badge.
7. **Faculty & expertise** — searchable grid (photo or initials avatar, name, position, top keywords); click → drawer with profile, topics, recent works.
8. **Collaboration** — top partner institutions and countries.
9. **Assistant** — floating chat panel (also openable from nav); suggested prompts include the two required questions; streams tokens; citations render as chips that open paper/faculty detail.
10. **Footer** — data sources (OpenAlex, provided dataset, Wikipedia/Commons) with links and licenses: render `institution.sources`
    verbatim; the Wikipedia description is CC BY-SA 4.0 and each Commons photo needs its `attribution` shown near the image.
    Images are hot-linked from Wikimedia (`thumb.wikimedia.org`, `upload.wikimedia.org`): add both hosts to `next/image` `remotePatterns`.

## UX standards
- Mobile-first; no horizontal scroll at 360px; charts resize.
- Every async block has skeleton, empty and error states.
- Dark mode via CSS variables (shadcn theme tokens); charts read colors from tokens.
- Accessibility: semantic landmarks, keyboard-navigable tabs/drawer/chat, focus rings, alt text, contrast AA, `aria-live` for streamed chat.
- Performance: server components for static sections, TanStack Query for client data, lazy-load ECharts and Leaflet, `next/image` for photos (configure remote domains).
- Numbers formatted with `Intl.NumberFormat`; dates `Intl.DateTimeFormat`.

## Structure
```
frontend/src/
  app/page.tsx              # composes sections
  components/sections/*     # Hero, Kpis, ResearchFocus, Output, Publications, Faculty, Collaboration
  components/chat/*         # ChatPanel, MessageList, CitationChip
  components/ui/*           # shadcn
  lib/api.ts                # typed client (types generated from backend OpenAPI)
  lib/sse.ts                # chat stream parser
```
