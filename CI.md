# CI — Corporate Identity

## Brand

| Key             | Value                                 |
|-----------------|---------------------------------------|
| Name            | **UTCC GS Chat**                      |
| Full name       | Graduate School — บัณฑิตวิทยาลัย      |
| Tagline         | Management Console                    |
| Logo mark       | Shield with "GS" + horse silhouette   |

---

## Color Palette

### Primary (Orange / Amber)

| Token              | Hex       | Usage                              |
|--------------------|-----------|------------------------------------|
| `--primary`        | `#d4891c` | Buttons, active nav, links         |
| `--primary-dark`   | `#b8741a` | Hover states                       |
| `--primary-light`  | `#f0a83c` | Badges, highlights                 |
| `--primary-gradient` | `#c77b18 → #e8a838 → #f5c563` | Login/Register background |

### Accent (Dark / Black)

| Token              | Hex       | Usage                              |
|--------------------|-----------|------------------------------------|
| `--accent`         | `#1a1a1a` | Sidebar bg, primary buttons, text  |
| `--accent-light`   | `#2d2d2d` | Button hover                       |

### Neutral

| Token              | Hex       | Usage                              |
|--------------------|-----------|------------------------------------|
| `--bg-main`        | `#f7f5f2` | Page background (warm gray)        |
| `--bg-card`        | `#ffffff` | Cards, panels                      |
| `--border`         | `#e5e2dc` | Borders, dividers                  |
| `--text-primary`   | `#1a1a1a` | Headings, body text                |
| `--text-secondary` | `#6b7280` | Labels, captions                   |
| `--text-muted`     | `#9ca3af` | Timestamps, placeholders           |

### Semantic

| Token         | Hex       | Usage            |
|---------------|-----------|------------------|
| `--success`   | `#10b981` | Connected, OK    |
| `--warning`   | `#f59e0b` | Pending, caution |
| `--danger`    | `#ef4444` | Error, offline   |
| `--info`      | `#3b82f6` | Info badges      |

---

## Typography

| Element        | Font                                     | Weight | Size   |
|----------------|------------------------------------------|--------|--------|
| Body           | Inter, Noto Sans Thai, sans-serif        | 400    | 14px   |
| Heading (h1)   | Inter                                    | 700    | 20-24px|
| Label          | Inter                                    | 600    | 12-13px|
| Monospace      | JetBrains Mono, monospace                | 400    | 13px   |

---

## Components

### Sidebar
- Background: `--accent` (#1a1a1a)
- Width: 260px
- Active nav item: `--primary` background
- Brand icon: `--primary` background, white text

### Login / Register
- Full-screen gradient background (`--primary-gradient`)
- Centered white card with 16px border-radius
- Logo: dark square with "GS", orange text

### Buttons

| Variant     | Background    | Text     | Border           |
|-------------|---------------|----------|------------------|
| Primary     | `--accent`    | white    | none             |
| Secondary   | transparent   | `--accent` | `--border`     |
| Filled      | `--accent`    | white    | none             |
| Outline     | white         | text     | `--border`       |
| Danger      | white         | red      | red              |

### Status Badges
- `online` — green bg
- `offline` — red bg
- `pending` — amber bg
- `active` — orange bg

---

## Spacing

| Token   | Value |
|---------|-------|
| xs      | 4px   |
| sm      | 8px   |
| md      | 16px  |
| lg      | 24px  |
| xl      | 32px  |
| 2xl     | 48px  |

## Border Radius

| Element    | Radius |
|------------|--------|
| Card       | 12px   |
| Button     | 8-10px |
| Input      | 10px   |
| Badge      | 20px   |
| Avatar     | 50%    |
| Logo icon  | 16-18px|
