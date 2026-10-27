"""
Print raw input so bridge.py / the Remote Script can be configured.

    uv run tools/discover.py pad       # DualSense button / hat / axis indices
    uv run tools/discover.py minilab   # MiniLab 3 knob channel, CC and value

Knob mode, from the values printed while turning a knob slowly:
- climbs/falls through 0..127 and stops at the ends   -> absolute
- hovers around 64 (65 = right, 63 = left)             -> relative_binary_offset
- 1 = right, 127 = left                                -> relative_two_compliment
- 1 = right, 65 = left                                 -> relative_signed_bit
"""
import os
import sys
import time

os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")


def pad():
    import pygame

    pygame.init()
    pygame.joystick.init()
    print("Press buttons, D-pad, move sticks. Ctrl+C to quit.")
    last_axis = {}
    joysticks = {}  # instance_id -> Joystick, kept referenced so SDL keeps it open
    while True:
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEADDED:
                js = pygame.joystick.Joystick(event.device_index)
                joysticks[js.get_instance_id()] = js
                print(f"Controller: {js.get_name()} "
                      f"({js.get_numbuttons()} buttons, {js.get_numaxes()} axes, {js.get_numhats()} hats)")
            elif event.type == pygame.JOYDEVICEREMOVED:
                joysticks.pop(event.instance_id, None)
            elif hasattr(event, "instance_id") and event.instance_id in joysticks:
                name = joysticks[event.instance_id].get_name()
                if event.type == pygame.JOYBUTTONDOWN:
                    print(f"[{name}] button {event.button}")
                elif event.type == pygame.JOYHATMOTION:
                    print(f"[{name}] hat {event.value}")
                elif event.type == pygame.JOYAXISMOTION:
                    # only print big moves so the stream stays readable
                    v = round(event.value, 1)
                    key = (event.instance_id, event.axis)
                    if abs(v) >= 0.5 and last_axis.get(key) != v:
                        print(f"[{name}] axis {event.axis}: {v:+.1f}")
                    last_axis[key] = v
        time.sleep(0.005)


def minilab():
    import mido

    names = mido.get_input_names()
    name = next((n for n in names if n.startswith("Minilab3 MIDI")), None)
    if name is None:
        print("MiniLab not found. Inputs:", names)
        return
    print(f"Listening on {name}. Turn knobs, turn and click the main encoder, press pads. Ctrl+C to quit.")
    # Poll instead of `for msg in port`: a blocking read can't be interrupted by Ctrl+C on Windows
    with mido.open_input(name) as port:
        while True:
            for msg in port.iter_pending():
                if msg.type == "control_change":
                    print(f"channel {msg.channel + 1}  CC {msg.control:3d}  value {msg.value:3d}")
                elif msg.type in ("note_on", "note_off"):
                    print(f"channel {msg.channel + 1}  {msg.type:8s} note {msg.note:3d}  velocity {msg.velocity:3d}")
                elif msg.type in ("program_change", "pitchwheel", "aftertouch", "polytouch"):
                    print(f"channel {msg.channel + 1}  {msg}")
            time.sleep(0.005)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "pad":
            pad()
        elif mode == "minilab":
            minilab()
        else:
            print(__doc__)
    except KeyboardInterrupt:
        pass
