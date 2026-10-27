# Mapping

Everything the rig does, in one place. Source of truth: the tables at the top of
[bridge.py](bridge.py) and [DualSenseArturia.py](remote_script/DualSenseArturia/DualSenseArturia.py).
Keep the channel, note and CC numbers in sync between the two files.

## DualSense (PS5)

| Control | Action |
|---|---|
| Left stick left / right | Session view: previous / next track |
| Left stick up / down | Arrangement view: previous / next track. Session view: select previous / next clip on the selected track (selection only, skips empty slots) |
| Right stick up / down | Volume of the selected track. Rate control: harder push is faster, holds on release |
| Lightbar | Color of the selected track (feedback from Live) |

Left stick: one step per flick, it must return to center before the next one. On a diagonal the
stronger direction wins.

Sent but not used by the script (free for manual MIDI mapping with Ctrl+M in Live, channel 16):
Cross (note 0), Circle (note 1), Square (note 2), Triangle (note 3), L1 (64), R1 (65), L3 (68), R3 (69), D-pad up/down/left/right
(70/71/72/73). L2 and R2 are not sent.

## Xbox controller

| Control | Action |
|---|---|
| Left stick up / down | Scene up / down (one step per flick) |
| B | Launch the selected clip only (not the whole scene). An empty slot does nothing |
| X | Play |
| Y | Pause (Live has no true pause: stops playback, keeps the position) |
| A | Stop (stops playback and returns to the start) |
| Back / View (small button left of the Xbox logo) | Stop clips on the selected track |
| RB | Stop all clips |

Not used: Start/Menu, LB, LT, RT, right stick, stick clicks, D-pad, Guide.

The selected clip is the highlighted slot: selected track (DualSense left/right) crossed with
selected scene (Xbox left stick, or DualSense up/down in session view).

## MiniLab 3

| Control | Action |
|---|---|
| Knobs 1-8 | Depends on the knob page, see below |
| Main encoder, press | Next knob page: DEVICE, SPLIT, LINK, MIX |
| Main encoder, turn | MIX page: pick the parameter (Volume, Macro 1-8). Other pages: nothing |
| Keys | Play the armed track directly (not through Python) |
| Pad Bank A, pads 1-8 | Drums. Straight to Live, notes 36-43 on channel 10 |
| Pad Bank B, pad 1 | Toggle the selected track's instrument (first rack's Device On) on/off |
| Pad Bank B, pad 2 | Hold: instrument on while pressed, off on release |
| Pad Bank B, pads 3-8 | Not used yet |

Knobs never set absolute values: each turn becomes a step added to the parameter's current
value, so nothing jumps when the target changes. Main encoder press cycles the knob page:

| Page | Knobs |
|---|---|
| DEVICE (start) | 1-8 = Macro 1-8 of the selected track |
| SPLIT | 1-4 = Macro 1-4 of the selected track, 5-8 = Macro 1-4 of the pinned track. Entering SPLIT pins the track selected at that moment |
| LINK | Knob N = Macro N of the selected and the pinned track together |
| MIX | Knob N = one parameter of track N, counting from the selected track. Main encoder turn picks the parameter: Volume, Macro 1-8 |

Returning to DEVICE unpins. To pin a different track, cycle back round to SPLIT with that track
selected. The Live status bar shows the page, pinned track and every value change.

`KNOB_MODE` in `bridge.py` must match the MiniLab: `"absolute"` (factory setting; a knob stops
at its end) or a relative mode after switching the knobs to relative in MIDI Control Center
(recommended, knobs turn without limit).

## MIDI messages

Port `ps5_midi` carries controls into Live. Port `ps5_feedback` carries the track color back.

### DualSense, channel 16

| Message | Number | Meaning |
|---|---|---|
| Note | 120 | Stick up |
| Note | 121 | Stick down |
| Note | 125 | Stick left |
| Note | 126 | Stick right |
| Note | 122 | Bridge to Live: resend the track color on start-up |
| CC | 23 | Right stick volume (64 = none, 127 = full up, 1 = full down) |
| CC | 20, 21, 22 | Live to bridge on `ps5_feedback`: track color R, G, B (7-bit each) |

### Xbox, channel 15

| Note | Meaning |
|---|---|
| 0 | X: play |
| 1 | Y: pause |
| 2 | A: stop |
| 3 | B: launch selected clip |
| 4 | Left stick up: scene up |
| 5 | Left stick down: scene down |
| 6 | Back / View: stop selected track |
| 7 | RB: stop all clips |

### MiniLab 3 Bank B pads, forwarded on channel 13

| Note | Meaning |
|---|---|
| 44 | Pad 1: toggle instrument |
| 45 | Pad 2: instrument on while held |
| 46-51 | Pads 3-8, forwarded but unused |

The bridge re-sends these from the MiniLab's channel 10 (notes 44-51). Bank A (notes 36-43) is not
forwarded. Bank B shares channel 10 with the drums, so those notes also reach any armed track listening
on channel 10, such as a Drum Rack (pads 9-16, silent while empty). Instrument tracks should listen to
Ch. 1 only.

### MiniLab 3, channel 1

CC 74, 71, 76, 77, 93, 18, 19, 16 for knobs 1-8, CC 114 main encoder turn, CC 115 press.
The bridge turns them into knob steps (below); none reach Live unchanged.

### Knob steps, bridge to Live, channel 11

CC value = 64 + step. Parameter p: 0 = volume, 1-8 = Macro 1-8 of the track's first rack.

| CC | Target |
|---|---|
| 0 + p | Selected track |
| 16 + p | Pinned track |
| 32 + 10k + p | Track k places right of the selected one (k = 0-7, MIX page) |

Notes on channel 11: 1 pin selected track, 2 unpin, 10-13 page changed (status), 20-28 MIX parameter changed (status). Numbers live in `knobs.py`; `tests/test_knobs.py` checks the Live script uses the same ones.

## Phone display feedback, Live to bridge

SysEx on the `ps5_feedback` port: `F0 7D <kind> <ASCII text> F7`. Kinds: `T` selected track name,
`P` pinned track name (empty = none), `V` value change as `track|parameter|value`. The bridge
serves them, with the knob page, at `http://<pc-ip>:8765`.

## Controller indices

Controllers are told apart by name (`DualSense`, `PS5`, `PlayStation` or `Xbox`). Button and axis
numbers come from SDL and can differ by driver. Confirm yours with
`uv run tools/discover.py pad` and edit the tables in `bridge.py` if they differ.

| Item | Assumed index | Confirmed |
|---|---|---|
| DualSense Cross / Circle / Square / Triangle | 0 / 1 / 2 / 3 | Circle and Triangle confirmed earlier |
| DualSense left stick X / Y | axis 0 / 1 | Yes |
| DualSense right stick Y | axis 3 | Yes |
| Xbox A / B / X / Y | 0 / 1 / 2 / 3 | Not checked |
| Xbox LB / RB / Back | 4 / 5 / 6 | Not checked |
| Xbox left stick Y | axis 1 | Yes |

## Live settings these depend on

| Item | Setting |
|---|---|
| Control Surface slot | DualSenseArturia, Input `ps5_midi`, Output `ps5_feedback` (shown in Live as "ps5_midi (Port 2)") |
| Input `ps5_midi` | Track Off, Remote On |
| Input `ps5_feedback` | Track Off, Remote Off |
| Input `Minilab3 MIDI` | Track On, Remote Off |

Live caches Remote Scripts: after `scripts/install.ps1`, restart Live.
