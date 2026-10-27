import json
import re
import threading
import urllib.request
from pathlib import Path

import display


def test_parse_status():
    assert display.parse_status([0x7D, ord("T")] + [ord(c) for c in "Lead"]) == ("track", "Lead")
    assert display.parse_status([0x7D, ord("P")]) == ("pinned", "")
    assert display.parse_status([0x7D, ord("V")] + [ord(c) for c in "Pad|Macro 1|64"]) == (
        "value", "Pad|Macro 1|64")


def test_parse_status_ignores_foreign_sysex():
    assert display.parse_status([0x00, 0x20, 0x6B, 0x7F]) is None  # Arturia
    assert display.parse_status([0x7D, ord("X"), 65]) is None  # unknown kind
    assert display.parse_status([0x7D]) is None


def test_state_versions_and_controllers():
    state = display.DisplayState()
    version, snap = state.snapshot()
    state.update(page="split")
    state.set_controller("dualsense", True)
    new_version, snap = state.wait(version, timeout=0)
    assert new_version > version
    assert snap["page"] == "split"
    assert snap["controllers"] == {"dualsense": True}


def test_wait_wakes_on_update():
    state = display.DisplayState()
    version, _ = state.snapshot()
    threading.Timer(0.05, lambda: state.update(track="Pad")).start()
    _, snap = state.wait(version, timeout=2)
    assert snap["track"] == "Pad"


def test_server_serves_page_and_events():
    state = display.DisplayState()
    state.update(track="Lead")
    server = display.start_server(state, port=0)  # any free port
    port = server.server_address[1]
    try:
        page = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=2).read().decode()
        assert "<title>Rig</title>" in page
        with urllib.request.urlopen("http://127.0.0.1:%d/events" % port, timeout=2) as events:
            line = events.readline().decode()
        assert line.startswith("data: ")
        assert json.loads(line[6:])["track"] == "Lead"
    finally:
        server.shutdown()


def test_live_script_uses_the_same_sysex_id():
    src = (Path(__file__).parent.parent / "remote_script/DualSenseArturia/DualSenseArturia.py").read_text(
        encoding="utf-8")
    assert int(re.search(r"^STATUS_SYSEX_ID = (0x[0-9A-Fa-f]+)", src, re.M).group(1), 16) == display.SYSEX_ID


def test_installable_app_files():
    import struct
    import zlib

    server = display.start_server(display.DisplayState(), port=0)
    base = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        manifest = json.loads(urllib.request.urlopen(base + "/manifest.webmanifest", timeout=2).read())
        assert manifest["start_url"] == "/" and manifest["display"] == "fullscreen"
        for icon in manifest["icons"]:
            png = urllib.request.urlopen(base + icon["src"], timeout=5).read()
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
            width, height = struct.unpack(">II", png[16:24])
            assert "%dx%d" % (width, height) == icon["sizes"]
            # IDAT decompresses to one filter byte + RGB per pixel, per row
            idat_len = struct.unpack(">I", png[33:37])[0]
            assert len(zlib.decompress(png[41:41 + idat_len])) == height * (1 + 3 * width)
        assert urllib.request.urlopen(base + "/sw.js", timeout=2).status == 200
        page = urllib.request.urlopen(base + "/", timeout=2).read().decode()
        assert 'rel="manifest"' in page
    finally:
        server.shutdown()


def test_snapshot_sysex_round_trip():
    state = display.DisplayState()
    state.update(track="Lead é", page="split", controllers={"phone": True})
    data = display.snapshot_sysex(state.snapshot()[1])
    assert data[:2] == [0x7D, ord("S")]
    assert all(0 <= b < 0x80 for b in data)  # valid inside SysEx
    decoded = json.loads(bytes(data[2:]).decode("ascii"))
    assert decoded["track"] == "Lead é" and decoded["page"] == "split"
