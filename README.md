# pyramidi

DualSense + MiniLab 3 performance rig for Ableton Live 12 (Windows, loopMIDI).

- Left stick up/down: select previous/next track (selection only, playback unaffected).
- Right stick up/down: volume of the selected track (rate control, speed follows deflection).
- MiniLab 3 knobs 1-8: Macro 1-8 of the first rack on the selected track, with pickup
  (a knob does nothing until it reaches the macro's current value, so sounds never jump).
- DualSense lightbar: color of the selected track.

```
DualSense ─┐
           ├─ bridge.py ─ loopMIDI "ps5_midi" ─ DualSenseArturia Remote Script
MiniLab 3 ─┘ (knob CCs only)
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

Script errors go to `%APPDATA%\Ableton\Live 12.x\Preferences\Log.txt` (search `DualSenseArturia`).
After editing the Remote Script, re-run `install.ps1` and restart Live (it caches scripts).
