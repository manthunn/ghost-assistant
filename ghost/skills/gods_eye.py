"""Drive God's Eye View, the live 3D globe (github.com/bilawalsidhu/gods-eye-view).

The app is a Vite dev server in a sibling checkout, Projects/gods-eye-view. It
exposes everything worth controlling through the URL hash: camera position,
sensor style, HUD, and which data layers are on. So Ghost never has to click
around inside the page. It starts the server if it is not already up, builds a
share link, and opens it. Re-opening with a new hash on an already running
server is instant.

Coordinates come from the model, not a geocoder. Gemini knows where Tokyo is
to four decimal places and the alternative is a network round trip to
Nominatim for every request.

The server is spawned detached for the same reason as timers: Ghost exits
after five minutes of silence and the globe should stay up. close_gods_eye
kills it by port, so it also catches a server started by hand from a
terminal.
"""
import os
import pathlib
import shutil
import socket
import subprocess
import time
import webbrowser

from . import register

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
GEV = ROOT.parent / "gods-eye-view"
PORT = 4173
URL = f"http://localhost:{PORT}/"

# Internal style name to URL token, from src/sharelink.js STYLE_TO_URL.
STYLES = {"normal": "normal", "crt": "crt", "retro": "crt", "nvg": "nvg",
          "night vision": "nvg", "surveillance": "nvg", "flir": "flir",
          "thermal": "flir", "anime": "anime", "noir": "noir", "snow": "snow"}

# Layer id to single-char token, from src/data/layerState.js LAYER_STATE_REGISTRY.
LAYERS = {"ships": "a", "vessels": "a", "ais": "a",
          "bikeshare": "b", "cctv": "c", "cameras": "c",
          "earthquakes": "e", "flights": "f", "planes": "f", "aircraft": "f",
          "dams": "q", "datacenters": "d", "fires": "w",
          "military": "m", "military awareness": "g", "military installations": "i",
          "bases": "i", "radio": "r", "launches": "x", "rockets": "x",
          "satellites": "s", "cables": "u", "undersea cables": "u",
          "traffic": "t"}

# Set by main.py to GhostUI.target once the face exists. The face is a point
# cloud globe of its own, so a request to look at a place turns it there too.
# Kept as a module attribute rather than an import of ui3d: skills must stay
# importable without a window, or load_all reports them disabled.
face_target = None

# Not every layer is useful at every altitude. Flights and satellites read
# fine from orbit; traffic and cctv only mean anything over a city.
DEFAULT_LAYERS = "f.s"


def _port_open():
    # Node 18+ resolves "localhost" to ::1 first, so vite usually listens on
    # IPv6 only. Probe both stacks or a running server looks stopped.
    for family, host in ((socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1")):
        try:
            with socket.socket(family) as s:
                s.settimeout(0.3)
                if s.connect_ex((host, PORT)) == 0:
                    return True
        except OSError:
            continue
    return False


def _start_server():
    """Detached vite so it outlives Ghost. Blocks until the port answers."""
    if not (GEV / "node_modules").is_dir():
        return (f"God's Eye View is not installed at {GEV}. Run `npm ci` there "
                "first.")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # Call the vite entry script directly: pythonw has no PATH to find npx.cmd
    # and no console for a .cmd shim to run in.
    vite = GEV / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")
    if not node:
        return "node is not on PATH, so God's Eye View cannot start."
    subprocess.Popen([node, str(vite)],
                     cwd=str(GEV), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    for _ in range(40):
        if _port_open():
            return None
        time.sleep(0.5)
    return "God's Eye View server did not come up within 20 seconds."


def _hash(lat, lon, alt, heading, pitch, style, layers, hud):
    tokens = []
    for name in (layers or "").replace(",", " ").split():
        tok = LAYERS.get(name.strip().lower())
        if tok and tok not in tokens:
            tokens.append(tok)
    parts = [f"lat={lat:.5f}", f"lon={lon:.5f}", f"alt={int(alt)}",
             f"heading={int(heading)}", f"pitch={int(pitch)}",
             f"style={STYLES.get((style or 'normal').lower(), 'normal')}",
             f"hv={1 if hud else 0}", "v=2",
             f"l={'.'.join(tokens) if tokens else DEFAULT_LAYERS}"]
    return "#" + "&".join(parts)


@register({"name": "open_gods_eye",
    "description": "Open God's Eye View, a live 3D globe showing real aircraft, "
                    "ships, satellites, earthquakes, fires, traffic and public "
                    "cameras, flown to a place. Use when the user asks to see "
                    "somewhere from above, watch flights or ships over a region, "
                    "look at the globe, or wants a 'satellite view' or 'spy "
                    "satellite' look at a location. Supply lat/lon yourself from "
                    "the place name. Calling it again while open just moves the "
                    "camera.",
    "parameters": {"type": "object", "properties": {
        "place": {"type": "string", "description": "spoken name, for the reply only, e.g. 'Tokyo'"},
        "lat": {"type": "number"},
        "lon": {"type": "number"},
        "alt": {"type": "number", "description": "camera height in metres. City ~3000, region ~200000, country ~2000000, whole globe ~20000000. Default 3000."},
        "heading": {"type": "number", "description": "compass degrees, default 0"},
        "pitch": {"type": "number", "description": "degrees, -90 is straight down, default -45"},
        "style": {"type": "string", "description": "normal, nvg (night vision), flir (thermal), crt, noir, snow, anime. Default normal."},
        "layers": {"type": "string", "description": "space separated: flights ships satellites earthquakes fires traffic cctv radio military bases launches cables datacenters dams bikeshare. Default 'flights satellites'."},
        "hud": {"type": "boolean", "description": "tactical intelligence HUD overlay, default true"}},
        "required": ["place", "lat", "lon"]}})
def open_gods_eye(place: str, lat: float, lon: float, alt: float = 3000,
                  heading: float = 0, pitch: float = -45, style: str = "normal",
                  layers: str = "", hud: bool = True):
    if face_target:
        try:
            face_target(lat, lon, place)
        except Exception:
            pass
    if not GEV.is_dir():
        return f"God's Eye View checkout missing at {GEV}."
    if not _port_open():
        err = _start_server()
        if err:
            return err
    webbrowser.open(URL + _hash(lat, lon, alt, heading, pitch, style, layers, hud))
    on = [n for n in (layers or "").replace(",", " ").split()
          if n.lower() in LAYERS] or ["flights", "satellites"]
    return (f"God's Eye View is over {place} at {int(alt)} metres, "
            f"{STYLES.get((style or 'normal').lower(), 'normal')} view, "
            f"layers: {', '.join(on)}.")


@register({"name": "close_gods_eye",
    "description": "Stop the God's Eye View server. The browser tab is left for the user to close.",
    "parameters": {"type": "object", "properties": {}}})
def close_gods_eye():
    if not _port_open():
        return "God's Eye View is not running."
    if os.name == "nt":
        pids = set()
        for proto in ("tcp", "tcpv6"):
            out = subprocess.run(["netstat", "-ano", "-p", proto],
                                 capture_output=True, text=True).stdout
            pids |= {ln.split()[-1] for ln in out.splitlines()
                     if f":{PORT} " in ln and "LISTENING" in ln}
        for pid in pids:
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                           capture_output=True)
    else:
        subprocess.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    return "God's Eye View stopped."
