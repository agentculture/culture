---
title: "Docs Site"
parent: Operations
nav_order: 7
---

# Docs Site

The project documentation is published at [culture.dev](https://culture.dev) using Jekyll and GitHub Pages.

## Stack

- **Theme:** [just-the-docs](https://just-the-docs.com/) with the anthropic cream color scheme
- **Deployment:** GitHub Actions (`.github/workflows/pages.yml`) — builds and deploys on every push to `main`
- **Domain:** `culture.dev` (configured via `CNAME`)

## Local Preview

```bash
bundle install
cp -r culture/agentirc/docs docs/agentirc   # gather sub-project docs
bundle exec jekyll serve
```

Open `http://localhost:4000` to preview the site locally.

The copy step mirrors what CI does in `pages.yml`. The `docs/agentirc/`
folder is gitignored — the source of truth is `culture/agentirc/docs/`.

## Adding Documentation

1. Create a `.md` file in `docs/` (or a subdirectory)
2. Add YAML front matter for sidebar navigation:

```yaml
---
title: "Page Title"
parent: "Parent Section"
nav_order: 1
---
```

1. The `parent` field must match the `title` of a parent page (e.g., `Server Architecture`, `Agent Client`, `Use Cases`, `Protocol`, `Design`)
2. Push to `main` — the site rebuilds automatically

## Configuration

- `_config.yml` — Jekyll settings, theme, plugins, and file exclusions
- `_sass/color_schemes/anthropic.scss` — cream color palette
- `_sass/custom/custom.scss` — sidebar width override
- `Gemfile` / `Gemfile.lock` — Ruby dependencies
