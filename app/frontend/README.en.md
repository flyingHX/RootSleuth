# RootSleuth Operations Console Frontend

English | [简体中文](README.md)

> Positioning: this directory contains the frontend of the RootSleuth operations console (React + Vite + shadcn/ui + Tailwind CSS). See the repository root `README.md` for the project overview, `docs/CONSOLE_USER_GUIDE.md` for the user guide, and the Help Center page at `/help` for in-app documentation.

## Tech Stack

- Vite / TypeScript / React
- shadcn/ui (components under `src/components/ui`)
- Tailwind CSS

## Directory Structure

```
app/frontend/
├── index.html         # HTML entry point
├── vite.config.ts     # Vite configuration
├── tailwind.config.ts # Tailwind configuration
├── package.json
└── src/
    ├── main.tsx       # Application entry point
    ├── App.tsx        # Router shell
    ├── pages/         # Pages (alert workbench, Agent workbench, knowledge base, rules, ops, help, etc.)
    ├── components/    # Business and UI components
    └── index.css      # Global styles and theme variables
```

## Environment Variables

```bash
cp .env.example .env   # VITE_API_BASE_URL points to the console backend address
```

## Development Commands

```bash
pnpm i              # Install dependencies
pnpm run dev        # Local development (defaults to port 3000, auto-avoids conflicts)
pnpm run lint       # ESLint check
pnpm run build      # Production build
```

## Notes

- The `@/` path alias points to the `src/` directory.
- Page routes are centralized in `src/App.tsx`; create new pages under `src/pages/` and register their routes there.
- The UI ships with Simplified Chinese as the default language and English as a secondary language (switchable from the top bar via the 中/EN toggle, persisted in localStorage with browser-language detection).
