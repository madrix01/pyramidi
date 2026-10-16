"""
DualSense + MiniLab 3 -> loopMIDI -> DualSenseArturia Remote Script (Live 12).

- Left stick up/down: one previous/next track tap per flick.
- Buttons / D-pad: note on/off on NAV_CHANNEL, for MIDI-mapping clips/scenes in Live.
- MiniLab 3 knobs: the 8 knob CCs are forwarded unchanged; everything else is ignored
  (keys/pads reach Live directly through the MiniLab's own port).

Keep the channel/note/CC constants in sync with
remote_script/DualSenseArturia/DualSenseArturia.py.

Run:  uv run bridge.py
"""
import ctypes
import os
import threading
import time

os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

import mido
import pygame

# Port names are matched by prefix: Windows appends an index ("ps5_midi 6")
# that can differ between input/output lists and between reboots.
OUT_PORT_PREFIX = "ps5_midi"
MINILAB_PORT_PREFIX = "Minilab3 MIDI"
# Live's script sends the selected track's color here (set as the script's Output in Live)
FEEDBACK_PORT_PREFIX = "ps5_feedback"

# MiniLab 3 knobs
KNOB_CHANNEL = 0  # MIDI channel 1
KNOB_CCS = (74, 71, 76, 77, 93, 18, 19, 16)

# DualSense
NAV_CHANNEL = 15  # MIDI channel 16, keeps clear of the MiniLab on channel 1
VELOCITY = 100
TRACK_PREV_NOTE = 120
TRACK_NEXT_NOTE = 121
REFRESH_NOTE = 122
COLOR_CCS = (20, 21, 22)  # R, G, B
IDLE_COLOR = (0, 0, 40)   # lightbar until Live reports a track color
LED_BRIGHTNESS = 1.0

LEFT_STICK_Y = 1   # up is negative
RIGHT_STICK_Y = 3  # up is negative

# Right stick = selected track volume, as a rate control: while deflected, a CC is sent
# every VOLUME_INTERVAL s (64 = none, 127 = full up, 1 = full down).
VOLUME_CC = 23
VOLUME_DEADZONE = 0.15
VOLUME_INTERVAL = 0.02
TRIGGER = 0.6      # stick past this fires a tap
REARM = 0.3        # stick must return inside this before the next tap

# Indices from tools/discover.py; edit if your driver reports different ones.
BUTTON_TO_NOTE = {
    0: 0,    # Cross
    1: 1,    # Circle
    2: 2,    # Square
    3: 3,    # Triangle
    9: 64,   # L1
    10: 65,  # R1
    7: 68,   # L3
    8: 69,   # R3
}

HAT_TO_NOTE = {
    (0, 1): 70,    # Up
    (0, -1): 71,   # Down
    (-1, 0): 72,   # Left
    (1, 0): 73,    # Right
}


def find_port(names, prefix):
    for name in names:
        if name.startswith(prefix):
            return name
    return None


class Output:
    """Thread-safe wrapper: the MiniLab callback runs on rtmidi's thread."""

    def __init__(self, port):
        self._port = port
        self._lock = threading.Lock()

    def send(self, msg):
        with self._lock:
            self._port.send(msg)

    def note(self, note, on):
        self.send(mido.Message(
            "note_on" if on else "note_off",
            note=note, velocity=VELOCITY if on else 0, channel=NAV_CHANNEL,
        ))

    def tap(self, note):
        self.note(note, True)
        self.note(note, False)

    def close(self):
        self._port.close()


class Lightbar:
    """DualSense lightbar through SDL2's SDL_JoystickSetLED (pygame doesn't expose it).

    SDL ref-counts joysticks, so opening the device again returns the same handle
    pygame already uses. Not thread-safe: call set() from the main loop only.
    """

    def __init__(self):
        self._sdl = ctypes.CDLL(os.path.join(os.path.dirname(pygame.__file__), "SDL2.dll"))
        self._sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
        self._sdl.SDL_JoystickOpen.argtypes = [ctypes.c_int]
        self._sdl.SDL_JoystickSetLED.argtypes = [ctypes.c_void_p] + [ctypes.c_uint8] * 3
        self._handle = None

    def attach(self, device_index):
        self._handle = self._sdl.SDL_JoystickOpen(device_index)

    def detach(self):
        self._handle = None

    def set(self, rgb):
        if self._handle:
            r, g, b = (min(255, int(c * LED_BRIGHTNESS)) for c in rgb)
            self._sdl.SDL_JoystickSetLED(self._handle, r, g, b)


class ColorFeedback:
    """Collects R/G/B CCs from Live on rtmidi's thread; main loop applies them."""

    def __init__(self):
        self._rgb = list(IDLE_COLOR)
        self.pending = None

    def on_message(self, msg):
        if msg.type != "control_change" or msg.channel != NAV_CHANNEL or msg.control not in COLOR_CCS:
            return
        self._rgb[COLOR_CCS.index(msg.control)] = min(255, msg.value * 2)
        if msg.control == COLOR_CCS[-1]:  # blue arrives last: color is complete
            self.pending = tuple(self._rgb)


def open_feedback(feedback):
    name = find_port(mido.get_input_names(), FEEDBACK_PORT_PREFIX)
    if name is None:
        print(f"No '{FEEDBACK_PORT_PREFIX}*' port: lightbar stays idle color. "
              "Create it in loopMIDI to enable track-color feedback.")
        return None
    print(f"Feedback: {name}")
    return mido.open_input(name, callback=feedback.on_message)


def open_minilab(out):
    name = find_port(mido.get_input_names(), MINILAB_PORT_PREFIX)
    if name is None:
        print(f"MiniLab not found (looked for '{MINILAB_PORT_PREFIX}*'). "
              "Running with DualSense only.")
        return None

    knobs = set(KNOB_CCS)

    def forward(msg):
        if msg.type == "control_change" and msg.channel == KNOB_CHANNEL and msg.control in knobs:
            out.send(msg)

    port = mido.open_input(name, callback=forward)
    print(f"MiniLab: {name}")
    return port


def run(out, lightbar, feedback):
    js = None
    right_y = 0.0
    last_volume_send = 0.0
    stick_ready = True
    last_hat = (0, 0)

    if pygame.joystick.get_count() == 0:
        print("Waiting for DualSense...")

    while True:
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEADDED and js is None:
                js = pygame.joystick.Joystick(event.device_index)
                print(f"Controller: {js.get_name()}")
                lightbar.attach(event.device_index)
                lightbar.set(IDLE_COLOR)
                out.tap(REFRESH_NOTE)  # ask Live for the current track color

            elif event.type == pygame.JOYDEVICEREMOVED and js is not None \
                    and event.instance_id == js.get_instance_id():
                js = None
                right_y = 0.0
                lightbar.detach()
                print("Controller disconnected. Waiting...")

            elif event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                note = BUTTON_TO_NOTE.get(event.button)
                if note is not None:
                    out.note(note, event.type == pygame.JOYBUTTONDOWN)

            elif event.type == pygame.JOYHATMOTION:
                old_note = HAT_TO_NOTE.get(last_hat)
                new_note = HAT_TO_NOTE.get(event.value)
                if old_note is not None:
                    out.note(old_note, False)
                if new_note is not None:
                    out.note(new_note, True)
                last_hat = event.value

            elif event.type == pygame.JOYAXISMOTION and event.axis == RIGHT_STICK_Y:
                right_y = event.value

            elif event.type == pygame.JOYAXISMOTION and event.axis == LEFT_STICK_Y:
                value = event.value
                if abs(value) < REARM:
                    stick_ready = True
                elif stick_ready and abs(value) > TRIGGER:
                    stick_ready = False
                    if value < 0:
                        out.tap(TRACK_PREV_NOTE)
                        print("Track previous")
                    else:
                        out.tap(TRACK_NEXT_NOTE)
                        print("Track next")

        now = time.monotonic()
        if abs(right_y) > VOLUME_DEADZONE and now - last_volume_send >= VOLUME_INTERVAL:
            last_volume_send = now
            # 0 at the edge of the deadzone, 1 at full deflection; up (negative) raises
            amount = (abs(right_y) - VOLUME_DEADZONE) / (1.0 - VOLUME_DEADZONE)
            direction = -1 if right_y > 0 else 1
            value = 64 + direction * round(63 * min(1.0, amount))
            out.send(mido.Message("control_change", control=VOLUME_CC, value=value, channel=NAV_CHANNEL))

        color, feedback.pending = feedback.pending, None
        if color is not None:
            lightbar.set(color)

        time.sleep(0.002)


def main():
    out_name = find_port(mido.get_output_names(), OUT_PORT_PREFIX)
    if out_name is None:
        print(f"No output port starting with '{OUT_PORT_PREFIX}'.")
        print("Available:", mido.get_output_names())
        return
    out = Output(mido.open_output(out_name))
    print(f"Output: {out_name}")

    minilab = open_minilab(out)

    pygame.init()
    pygame.joystick.init()
    lightbar = Lightbar()
    feedback = ColorFeedback()
    feedback_port = open_feedback(feedback)
    print("Bridge running. Ctrl+C to stop.")
    try:
        run(out, lightbar, feedback)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        for port in (minilab, feedback_port):
            if port is not None:
                port.close()
        out.close()
        pygame.quit()


if __name__ == "__main__":
    main()
