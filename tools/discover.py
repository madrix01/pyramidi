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
    while True:
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEADDED:
                js = pygame.joystick.Joystick(event.device_index)
                print(f"Controller: {js.get_name()} "
                      f"({js.get_numbuttons()} buttons, {js.get_numaxes()} axes, {js.get_numhats()} hats)")
            elif event.type == pygame.JOYBUTTONDOWN:
                print(f"button {event.button}")
            elif event.type == pygame.JOYHATMOTION:
                print(f"hat {event.value}")
            elif event.type == pygame.JOYAXISMOTION:
                # only print big moves so the stream stays readable
                v = round(event.value, 1)
                if abs(v) >= 0.5 and last_axis.get(event.axis) != v:
                    print(f"axis {event.axis}: {v:+.1f}")
                last_axis[event.axis] = v
        time.sleep(0.005)


def minilab():
    import mido

    names = mido.get_input_names()
    name = next((n for n in names if n.startswith("Minilab3 MIDI")), None)
    if name is None:
        print("MiniLab not found. Inputs:", names)
        return
    print(f"Listening on {name}. Turn each knob 1-8 in order. Ctrl+C to quit.")
    with mido.open_input(name) as port:
        for msg in port:
            if msg.type == "control_change":
                print(f"channel {msg.channel + 1}  CC {msg.control:3d}  value {msg.value:3d}")


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
