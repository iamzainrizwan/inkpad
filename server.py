#!/usr/bin/env python3
"""inkpad: write on the iPad, see it live on the laptop.

stdlib only. Run:  python3 tools/inkpad/server.py  [--port 8765]
Open the printed URL on both devices (iPad over Tailscale or the same wifi).
Notebook is saved to tools/inkpad/notebook.json.
"""
import argparse, json, os, queue, shutil, struct, subprocess, tempfile, threading, time, uuid
from urllib.parse import parse_qs, unquote, urlparse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("INKPAD_DATA", os.path.join(HERE, "notebook.json"))
INDEX = os.path.join(HERE, "index.html")
IMG_DIR = os.path.join(os.path.dirname(DATA), "img")
MAX_UPLOAD = 150 * 1024 * 1024
SLIDE_W, SLIDE_X, SLIDE_Y = 720, 140, 80   # slide box on the 1000-wide page: room all round to annotate
DECK_EXT = {".pptx", ".ppt", ".odp", ".key", ".pdf"}

lock = threading.Lock()
clients = set()  # queues


def new_page(title="Untitled"):
    return {"id": uuid.uuid4().hex[:8], "title": title, "bg": "grid", "strokes": []}


def load():
    if os.path.exists(DATA):
        with open(DATA) as f:
            return json.load(f)
    return {"pages": [new_page("Page 1")]}


nb = load()
dirty = False


def saver():
    global dirty
    while True:
        time.sleep(1.5)
        with lock:
            if not dirty:
                continue
            snap = json.dumps(nb)
            dirty = False
        tmp = DATA + ".tmp"
        with open(tmp, "w") as f:
            f.write(snap)
        os.replace(tmp, DATA)


def page(pid):
    for p in nb["pages"]:
        if p["id"] == pid:
            return p
    return None


def apply(op):
    """Mutate the notebook; return False if op should not be broadcast."""
    global dirty
    t = op.get("type")
    if t == "live":  # in-progress stroke chunk: relay only
        return True
    p = page(op.get("page")) if "page" in op else None
    if t == "stroke" and p:
        p["strokes"].append(op["stroke"])
    elif t == "erase" and p:
        ids = set(op["ids"])
        p["strokes"] = [s for s in p["strokes"] if s["id"] not in ids]
    elif t == "restore" and p:  # undo of an erase
        p["strokes"].extend(op["strokes"])
    elif t == "clear" and p:
        p["strokes"] = []
    elif t == "bg" and p:
        p["bg"] = op["bg"]
    elif t == "rename" and p:
        p["title"] = op["title"][:80]
    elif t == "addpage":
        np_ = new_page(op.get("title") or f"Page {len(nb['pages']) + 1}")
        if op.get("id"):
            np_["id"] = op["id"]
        nb["pages"].append(np_)
        op["pageObj"] = np_
    elif t == "delpage" and p and len(nb["pages"]) > 1:
        nb["pages"].remove(p)
        if p.get("img"):
            try:
                os.remove(os.path.join(IMG_DIR, os.path.basename(p["img"]["src"])))
            except OSError:
                pass
    elif t == "goto":
        pass  # "follow me" signal, relay only
    else:
        return False
    if t != "goto":
        dirty = True
    return True


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def deck_to_pngs(src, work):
    """pptx/odp/key -> pdf (LibreOffice) -> one png per slide (poppler). Returns sorted png paths."""
    pdf = src
    if not src.lower().endswith(".pdf"):
        profile = tempfile.mkdtemp(prefix="lo-", dir=work)
        subprocess.run(["soffice", "--headless", "--norestore", f"-env:UserInstallation=file://{profile}",
                        "--convert-to", "pdf", "--outdir", work, src],
                       check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pdf = os.path.splitext(src)[0] + ".pdf"
        if not os.path.exists(pdf):
            raise RuntimeError("LibreOffice couldn't convert that file")
    subprocess.run(["pdftoppm", "-png", "-scale-to-x", "1600", "-scale-to-y", "-1", pdf,
                    os.path.join(work, "slide")], check=True, timeout=180)
    return sorted((os.path.join(work, f) for f in os.listdir(work) if f.startswith("slide") and f.endswith(".png")),
                  key=lambda p: int(p.rsplit("-", 1)[1][:-4]))


def import_deck(filename, body):
    """Turn an uploaded deck into pages: one per slide, slide centred with margins."""
    stem, ext = os.path.splitext(os.path.basename(filename) or "slides.pdf")
    if ext.lower() not in DECK_EXT:
        raise ValueError(f"can't import {ext or 'that'} files: use .pptx, .odp, .key or .pdf")
    os.makedirs(IMG_DIR, exist_ok=True)
    deck = uuid.uuid4().hex[:8]
    work = tempfile.mkdtemp(prefix="deck-")
    try:
        src = os.path.join(work, "deck" + ext.lower())
        with open(src, "wb") as f:
            f.write(body)
        pngs = deck_to_pngs(src, work)
        pages = []
        for n, png in enumerate(pngs, 1):
            name = f"{deck}-{n}.png"
            shutil.move(png, os.path.join(IMG_DIR, name))
            iw, ih = png_size(os.path.join(IMG_DIR, name))
            pg = new_page(f"{n} · {stem}")
            pg["bg"] = "blank"
            pg["img"] = {"src": f"img/{name}", "x": SLIDE_X, "y": SLIDE_Y, "w": SLIDE_W,
                         "h": round(SLIDE_W * ih / iw, 1)}
            pages.append(pg)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not pages:
        raise RuntimeError("no slides found in that file")
    global dirty
    with lock:
        nb["pages"].extend(pages)
        dirty = True
    for pg in pages:
        broadcast({"type": "addpage", "from": "server", "pageObj": pg})
    return pages


def broadcast(msg):
    data = json.dumps(msg)
    for q in list(clients):
        q.put(data)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(INDEX, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path.startswith("/img/"):
            name = os.path.basename(unquote(path))
            fp = os.path.join(IMG_DIR, name)
            if not name.endswith(".png") or not os.path.isfile(fp):
                return self._send(404, "not found", "text/plain")
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)
        elif path == "/state":
            with lock:
                self._send(200, json.dumps(nb), "application/json")
        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            q = queue.Queue()
            clients.add(q)
            try:
                self.wfile.write(b": hi\n\n")
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(f"data: {data}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                clients.discard(q)
                self.close_connection = True
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/upload":
            n = int(self.headers.get("Content-Length", 0))
            if not 0 < n <= MAX_UPLOAD:
                return self._send(413, json.dumps({"error": "file too big (150MB max)"}), "application/json")
            body = self.rfile.read(n)
            name = parse_qs(u.query).get("name", ["slides.pdf"])[0]
            try:
                pages = import_deck(name, body)
            except (ValueError, RuntimeError) as e:
                return self._send(400, json.dumps({"error": str(e)}), "application/json")
            except (subprocess.SubprocessError, OSError) as e:
                return self._send(500, json.dumps({"error": f"conversion failed: {e.__class__.__name__}"}),
                                  "application/json")
            return self._send(200, json.dumps({"pages": [p["id"] for p in pages]}), "application/json")
        if self.path != "/op":
            return self._send(404, "not found", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        try:
            op = json.loads(self.rfile.read(n))
        except ValueError:
            return self._send(400, "bad json", "text/plain")
        with lock:
            ok = apply(op)
        if ok:
            broadcast(op)
        self._send(200, json.dumps({"ok": ok, "op": op if op.get("type") == "addpage" else None}),
                   "application/json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    threading.Thread(target=saver, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    srv.daemon_threads = True
    print(f"inkpad on http://localhost:{a.port}  (iPad: http://<tailscale-ip>:{a.port} via tailscale, "
          f"or http://<lan-ip>:{a.port} on home wifi)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
