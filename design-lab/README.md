# FinPilot design lab (A2.5 phase 1)

Static, throwaway prototypes for choosing a visual direction. They are not part of the app.

- No build step and no new dependencies. Plain HTML, CSS, and JavaScript.
- Illustrative data only, from `shared/lab.js`. No backend, database, or Google calls.
- The Google button only switches the preview. There are no auth endpoints here.
- Fonts load from Google Fonts for convenience. The real app self-hosts fonts with `next/font`.

## View locally

```
cd design-lab
uv run --no-project python -m http.server 4173 --bind 127.0.0.1
```

Then open http://127.0.0.1:4173/. Any static file server works.

| Concept | URL |
|---|---|
| A · Margin | http://127.0.0.1:4173/margin/index.html |
| B · Calibre | http://127.0.0.1:4173/calibre/index.html |
| C · Waypoint | http://127.0.0.1:4173/waypoint/index.html |

Add `#landing`, `#signin`, or `#app` to open a view directly. The top bar switches view, language, theme, motion, and sign-in state.

Screenshots from the browser review are in `screenshots/`. Delete this folder once a direction is chosen.
