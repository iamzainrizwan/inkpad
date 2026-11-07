# inkpad

a mini onenote for tutoring: write on the ipad with the pencil, it shows up live on
the laptop (and anything else that has the page open).

- pen (pressure), highlighter, stroke eraser, undo, pages, grid / lined / blank paper
- **follow**: other devices jump to the page and spot you're writing on
- **slides**: import a .pptx / .odp / .key / .pdf and it becomes one deck in the
  sidebar; flip slides with the bar at the bottom (or ← →). each slide sits with
  space all round to annotate, and keeps its own writing
- export a page as png

stdlib python server (`server.py`) + one html page (`index.html`). live updates over
server-sent events, strokes posted back as small ops. decks go pptx → pdf
(LibreOffice) → png per slide (poppler), so the docker image carries both.

## run

```
python3 server.py --port 8765        # local, no slide import unless soffice + pdftoppm exist
./deploy.sh                          # build + restart the container on alexandria
```

served at `http://alexandria.local/inkpad/` (nginx proxies `/inkpad/` to port 5155 with
buffering off for the live stream and `client_max_body_size 150m` for decks). data lives
in `~/inkpad/data` on alexandria: `notebook.json` plus `img/` for slide images.
