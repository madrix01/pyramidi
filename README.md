# pyramidi

DualSense + MiniLab 3 performance rig for Ableton Live 12 (Windows, loopMIDI).

DualSense:
- Left stick, arrangement view: up/down = previous/next track.
- Left stick, session view: left/right = previous/next track, up/down = select previous/next clip on the
  selected track (selection only, skips empty slots).
- Right stick up/down: volume of the selected track (rate control, speed follows deflection).
- Lightbar: color of the selected track.

Xbox controller:
- Left stick up/down: scene up/down. B launches the selected clip (single clip, not the scene).
- X play, Y pause, A stop (Live has no true pause: it stops and keeps the position).
- Back: stop clips on the selected track.

MiniLab 3 pads: Bank A plays drums. Bank B pad 1 toggles the selected track's instrument
(rack Device On) on/off, pad 2 holds it on while pressed.

MiniLab 3 knobs 1-8: knob pages, cycled with the main encoder press. DEVICE (Macro 1-8 of the
selected track), SPLIT (1-4 selected track, 5-8 a pinned track), LINK (each knob moves the same
macro on both), MIX (one parameter across 8 tracks). Knobs move parameters by steps, so nothing
jumps. Details in `mapping.md`.

Phone display: the bridge serves a live status page (knob page, selected and pinned track, last
value change, controller connections). Open the `Display:` address the bridge prints, from any
browser on the same network. See "Phone display" below.

Controllers are told apart by name (see `classify` in `bridge.py`). Button and axis indices come
from `uv run tools/discover.py pad`; edit the tables at the top of `bridge.py` if yours differ.

```
DualSense ─┐
           ├─ bridge.py ─ loopMIDI "ps5_midi" ─ DualSenseArturia Remote Script
MiniLab 3 ─┘ (knob steps, encoder, Bank B pads)
MiniLab 3 keys/pads ─ "Minilab3 MIDI" ─ Live tracks (direct)
```

## Setup

1. `uv sync`
2. `uv run tools/discover.py pad` and `uv run tools/discover.py minilab`. Update constants in
   `bridge.py` and `remote_script/DualSenseArturia/DualSenseArturia.py` if anything differs.
3. `powershell -ExecutionPolicy Bypass -File scripts/install.ps1`, then restart Live.
4. In loopMIDI create two ports: `ps5_midi` (controls into Live) and `ps5_feedback` (track color back
   to the bridge). Windows may show the second one in Live as "ps5_midi (Port 2)".
5. Live → Settings → Link, Tempo & MIDI:

   | Item | Setting |
   |---|---|
   | Control Surface slot 1 (currently MiniLab_3) | None |
   | Control Surface slot 2 | DualSenseArturia, Input `ps5_midi`, Output `ps5_feedback` |
   | Input `ps5_midi` | Track Off, Remote On |
   | Input `ps5_feedback` | Track Off, Remote Off |
   | Input `Minilab3 MIDI` | Track On, Remote Off |

6. One MIDI track per instrument, each with an Instrument Rack (macros 1-8 assigned) as its first rack.
7. `uv run bridge.py`, or double-click `start-bridge.bat`

## Phone app (USB, no Wi-Fi)

`android/` is a small Android app that shows the same display over a USB cable: the phone runs
in USB MIDI mode, so Windows sees it as a MIDI device and the bridge sends it the display state
as SysEx (on every change, plus every 2 s). No web server, Wi-Fi or developer mode needed.

1. Build: `powershell -ExecutionPolicy Bypass -File scripts/build-app.ps1`
   (APK lands in `android/app/build/outputs/apk/debug/app-debug.apk`).
2. Install: copy the APK to the phone and open it, or with USB debugging on, run the script with
   `-Install`.
3. Plug the phone into the PC, tap the USB notification, choose **MIDI**, open the Rig app.
4. Start the bridge. It prints `Phone: <port name>` when it finds the phone. If it doesn't, add a
   word from the phone's port name (it prints all ports) to `PHONE_PORT_HINTS` in `bridge.py`.

## Phone display (browser, Wi-Fi)

1. Put the phone and the PC on the same network. For gigs, use the phone's hotspot or USB
   tethering rather than venue Wi-Fi.
2. Start the bridge. It prints one `Display: http://<ip>:8765` line per network adapter; use the
   one on the phone's network (usually `192.168.x.x` of the Wi-Fi or hotspot adapter).
3. Allow Python through Windows Firewall when asked (private networks).
4. Install it: in Chrome, menu > "Add to Home screen". It gets the Rig knob icon.
   - Over Wi-Fi this is a shortcut: it opens in Chrome, and one tap anywhere goes full screen.
     Turn on "stay awake while charging" on the phone, since the screen-on lock needs a secure
     address.
   - For a true app install (opens full screen, keeps the screen on by itself), Chrome needs a
     secure address. Either connect over USB (`adb reverse tcp:8765 tcp:8765`, then open
     `http://localhost:8765`), or on the phone open `chrome://flags`, enable
     "Insecure origins treated as secure" and add `http://<pc-ip>:8765`.

Script errors go to `%APPDATA%\Ableton\Live 12.x\Preferences\Log.txt` (search `DualSenseArturia`).
After editing the Remote Script, re-run `install.ps1` and restart Live (it caches scripts).
