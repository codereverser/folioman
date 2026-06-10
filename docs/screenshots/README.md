# Screenshots

Marketing screenshots embedded in the top-level [`README.md`](../../README.md).
Keep the **files here** and reference them with relative links — don't inline
images in the README.

Add these PNGs (the README's "A look inside" grid expects exactly these names):

| File | Screen |
|------|--------|
| `dashboard.png` | An investor dashboard — net-worth chart + metric cards |
| `capital-gains.png` | The capital-gains worksheet for a financial year |
| `integrity.png` | The holdings integrity / reconciliation view |
| `family.png` | The family aggregate view |

## How to capture

Use the seeded demo data so the shots look rich:

1. Open the live demo — [folioman.atomcoder.com](https://folioman.atomcoder.com/),
   sign in with `demo` / `demo-password` — **or** run locally after
   `python app/manage.py seed_demo` (see `docs/developer/`).
2. Use a ~1440px-wide window, light theme, and select the "Arjun Sharma" investor
   (the one with the multi-year ledger and capital gains).
3. Screenshot each screen above; crop to the content, and compress the PNGs so
   they stay small (e.g. `pngquant` / `oxipng`).
