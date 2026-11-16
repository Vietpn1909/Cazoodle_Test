---
paths:
  - "frontend/**"
---

# Frontend rules

- Read `.claude/docs/frontend.md` before adding or restyling sections.
- Next.js 15 App Router, TypeScript strict, `pnpm`. Server Components by default; add `"use client"` only where interactivity is needed.
- Styling: Tailwind + shadcn/ui components; colors only via theme tokens (CSS variables) so dark mode works. No hard-coded hex in components.
- Data: typed client in `src/lib/api.ts` using types generated from backend OpenAPI (`pnpm gen:api`); TanStack Query for client fetching. Never hand-write duplicate response types.
- Every data component handles loading (skeleton), empty and error states.
- Accessibility: keyboard reachable controls, visible focus, `alt` on images, `aria-live="polite"` on streamed chat output.
- Heavy libs (ECharts, Leaflet) loaded with `next/dynamic` and `ssr: false`.
- Run `pnpm lint` and `pnpm typecheck` before finishing a change.
