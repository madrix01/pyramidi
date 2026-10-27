"""
Phone / browser display. bridge.py keeps a DisplayState up to date and serves it on the local
network; any browser on the same Wi-Fi (or the phone's hotspot) opens http://<pc-ip>:8765.

The page gets updates pushed over Server-Sent Events, so it reacts instantly and reconnects on
its own if the bridge restarts. Standard library only.

Live's script reports what only Live knows (track names, values) as SysEx on the feedback port:
    F0 7D <kind> <ASCII text> F7      kind: T selected track, P pinned track, V value change
"""
import functools
import json
import socket
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
SYSEX_ID = 0x7D  # MIDI "non-commercial / educational" manufacturer ID
STATUS_KINDS = {"T": "track", "P": "pinned", "V": "value"}


SNAPSHOT_KIND = ord("S")  # bridge to phone app: F0 7D 'S' <JSON> F7


def snapshot_sysex(snapshot):
    """Display state as SysEx data bytes for the phone app. ensure_ascii keeps every byte < 0x80."""
    text = json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"))
    return [SYSEX_ID, SNAPSHOT_KIND] + list(text.encode("ascii"))


def parse_status(data):
    """SysEx data bytes (without F0/F7) -> (field, text), or None if it isn't ours."""
    if len(data) < 2 or data[0] != SYSEX_ID:
        return None
    field = STATUS_KINDS.get(chr(data[1]))
    if field is None:
        return None
    return field, "".join(chr(b) for b in data[2:])


class DisplayState:
    def __init__(self):
        self._cond = threading.Condition()
        self._version = 0
        self._state = {
            "page": "device", "mix_param": "Volume", "track": "", "color": "#333333",
            "pinned": "", "value": "", "controllers": {},
        }

    def update(self, **changes):
        with self._cond:
            self._state.update(changes)
            self._version += 1
            self._cond.notify_all()

    def set_controller(self, name, connected):
        with self._cond:
            controllers = dict(self._state["controllers"])
            controllers[name] = connected
        self.update(controllers=controllers)

    def snapshot(self):
        with self._cond:
            return self._version, dict(self._state)

    def wait(self, version, timeout):
        """Block until the state is newer than `version` (or timeout). Returns the snapshot."""
        with self._cond:
            self._cond.wait_for(lambda: self._version != version, timeout)
            return self._version, dict(self._state)


# ---------- installable web app (manifest, icons, service worker) ----------

MANIFEST = json.dumps({
    "name": "Rig Display", "short_name": "Rig", "start_url": "/", "scope": "/",
    "display": "fullscreen", "orientation": "any",
    "background_color": "#0b0b0d", "theme_color": "#0b0b0d",
    "icons": [{"src": "/icon-%d.png" % n, "sizes": "%dx%d" % (n, n), "type": "image/png",
               "purpose": "any maskable"} for n in (192, 512)],
})

# No caching: the page is only useful with a live bridge, and a stale copy would hide that.
SERVICE_WORKER = "self.addEventListener('fetch', () => {});\n"


@functools.lru_cache(maxsize=None)
def icon_png(size):
    """App icon drawn in code (no image files): amber knob with a pointer on the page background."""
    bg, amber = (0x0B, 0x0B, 0x0D), (0xFF, 0xB0, 0x00)
    c = (size - 1) / 2
    outer, inner = size * 0.34, size * 0.26
    rows = []
    for y in range(size):
        row = bytearray(b"\0")  # PNG filter byte: none
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            pointer = abs(x - c) < size * 0.035 and c - outer < y < c - inner * 0.2
            row += bytes(amber if (inner < d < outer) or pointer else bg)
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def lan_addresses():
    try:
        return sorted({a for a in socket.gethostbyname_ex(socket.gethostname())[2]
                       if not a.startswith("127.")})
    except OSError:
        return []


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, address reuse lets a second program bind the same port silently, and requests
    # then go to whichever one Windows picks. Fail loudly instead.
    allow_reuse_address = False


def start_server(state, port=PORT):
    class Handler(BaseHTTPRequestHandler):
        # Send each update immediately. Otherwise Windows holds small writes back for up to
        # ~200 ms waiting to batch them (Nagle's algorithm), which shows as lag on the phone.
        disable_nagle_algorithm = True

        def do_GET(self):
            if self.path == "/events":
                self._events()
            elif self.path in ("/", "/index.html"):
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/manifest.webmanifest":
                self._send(MANIFEST.encode("utf-8"), "application/manifest+json")
            elif self.path == "/sw.js":
                self._send(SERVICE_WORKER.encode("utf-8"), "text/javascript")
            elif self.path in ("/icon-192.png", "/icon-512.png"):
                self._send(icon_png(int(self.path[6:9])), "image/png")
            else:
                self.send_error(404)

        def _send(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            version, snap = state.snapshot()
            try:
                while True:
                    self.wfile.write(("data: %s\n\n" % json.dumps(snap)).encode("utf-8"))
                    self.wfile.flush()
                    # Wake on change, or every 15 s to resend (keeps the connection alive)
                    version, snap = state.wait(version, 15)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # phone closed the page or lost Wi-Fi

        def log_message(self, *args):
            pass  # keep the bridge console readable

    server = _Server(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Rig</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="theme-color" content="#0b0b0d">
<meta name="mobile-web-app-capable" content="yes">
<style>
  :root { --bg:#0b0b0d; --fg:#f2f2f2; --dim:#8a8a90; --accent:#ffb000; --line:#222226; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:16px/1.2 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         padding:max(16px, env(safe-area-inset-top)) 16px 16px; min-height:100vh;
         display:flex; flex-direction:column; gap:14px; }
  .bar { height:10px; border-radius:5px; background:#333; transition:background .2s; }
  .label { color:var(--dim); font-size:13px; letter-spacing:.08em; text-transform:uppercase; }
  .page { font-size:clamp(48px, 16vw, 120px); font-weight:800; color:var(--accent); }
  .big { font-size:clamp(28px, 8vw, 56px); font-weight:700; overflow-wrap:anywhere; }
  .row { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .card { border:1px solid var(--line); border-radius:12px; padding:12px; }
  .value { font-size:clamp(22px, 6vw, 40px); font-variant-numeric:tabular-nums; min-height:1.2em; }
  .status { display:flex; gap:10px; flex-wrap:wrap; font-size:13px; color:var(--dim); margin-top:auto; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:#c33; margin-right:5px; }
  .on .dot { background:#3c3; }
  #offline { display:none; color:#f55; font-weight:700; }
  body.full #hint { display:none; }
  body.offline #offline { display:block; }
</style></head>
<body>
  <div class="bar" id="color"></div>
  <div><div class="label">Knob page</div><div class="page" id="page">-</div><div id="pageinfo" class="label"></div></div>
  <div class="row">
    <div class="card"><div class="label">Selected (knobs 1-4 in split)</div><div class="big" id="track">-</div></div>
    <div class="card"><div class="label">Pinned (knobs 5-8 in split)</div><div class="big" id="pinned">-</div></div>
  </div>
  <div class="card"><div class="label">Last change</div><div class="value" id="value"></div></div>
  <div id="offline">Bridge not reachable, retrying...</div>
  <div id="hint" class="label">Tap anywhere for full screen</div>
  <div class="status" id="controllers"></div>
<script>
  const $ = id => document.getElementById(id);
  const INFO = { device: "Knobs 1-8: selected track", split: "1-4 selected, 5-8 pinned",
                 link: "Each knob moves both tracks" };
  function render(s) {
    $("page").textContent = s.page.toUpperCase();
    $("pageinfo").textContent = s.page === "mix" ? "Knob N = track N, " + s.mix_param : (INFO[s.page] || "");
    $("track").textContent = s.track || "-";
    $("pinned").textContent = s.pinned || "-";
    $("value").textContent = s.value.split("|").join("  \\u00b7  ");
    $("color").style.background = s.color;
    $("controllers").innerHTML = Object.entries(s.controllers)
      .map(([n, on]) => '<span class="' + (on ? "on" : "") + '"><span class="dot"></span>' + n + "</span>").join("");
  }
  // Installed app (secure origin such as localhost over USB): register the service worker
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js");

  // Full screen: automatic when installed, otherwise one tap (works over plain Wi-Fi too)
  const standalone = matchMedia("(display-mode: fullscreen), (display-mode: standalone)").matches;
  if (standalone) document.body.classList.add("full");
  document.addEventListener("click", () => {
    if (!document.fullscreenElement && document.documentElement.requestFullscreen)
      document.documentElement.requestFullscreen().then(() => document.body.classList.add("full")).catch(() => {});
  });
  document.addEventListener("fullscreenchange", () =>
    document.body.classList.toggle("full", standalone || !!document.fullscreenElement));

  // Keep the screen on while the page is visible (browser allows this on secure origins only)
  async function keepAwake() { try { await navigator.wakeLock.request("screen"); } catch (e) {} }
  if ("wakeLock" in navigator) {
    keepAwake();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) keepAwake(); });
  }

  const events = new EventSource("/events");
  events.onmessage = e => { document.body.classList.remove("offline"); render(JSON.parse(e.data)); };
  events.onerror = () => document.body.classList.add("offline");
</script>
</body></html>
"""


if __name__ == "__main__":
    # Standalone: serve the page with sample data, to check the phone can reach this PC
    # without starting the bridge, Live or any controller.
    import time

    state = DisplayState()
    state.update(track="Lead (sample)", pinned="Pad (sample)", color="#18e8fe",
                 value="Lead|Macro 1|64", controllers={"standalone test": True})
    server = start_server(state)
    for address in lan_addresses() or ["<this-pc-ip>"]:
        print(f"Display: http://{address}:{PORT}")
    print("Serving sample data. Ctrl+C to stop.")
    try:
        pages = ("device", "split", "link", "mix")
        tick = 0
        while True:  # cycle pages every 2 s so the phone visibly updates
            state.update(page=pages[tick % len(pages)])
            tick += 1
            time.sleep(2)
    except KeyboardInterrupt:
        server.shutdown()
