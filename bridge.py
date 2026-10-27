"""
DualSense + MiniLab 3 -> loopMIDI -> DualSenseArturia Remote Script (Live 12).

- Left stick up/down: one previous/next track tap per flick.
- Buttons / D-pad: note on/off on NAV_CHANNEL, for MIDI-mapping clips/scenes in Live.
- MiniLab 3 knobs: turned into steps and routed by the active knob page (see knobs.py).
  Main encoder press cycles pages, turn picks the mix parameter. Bank B pads are forwarded;
  keys and Bank A pads reach Live directly through the MiniLab's own port.

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

import display
import knobs

# Port names are matched by prefix: Windows appends an index ("ps5_midi 6")
# that can differ between input/output lists and between reboots.
OUT_PORT_PREFIX = "ps5_midi"
MINILAB_PORT_PREFIX = "Minilab3 MIDI"
# Live's script sends the selected track's color here (set as the script's Output in Live)
FEEDBACK_PORT_PREFIX = "ps5_feedback"
# Phone app (android/) in USB MIDI mode. Android usually shows up as "MIDI function"; any output port
# whose name contains one of these (case-insensitive) is treated as the phone. Add your phone's
# name here if it isn't found (the bridge prints all ports while it's looking).
PHONE_PORT_HINTS = ("midi function", "android", "pixel", "galaxy", "samsung", "xiaomi", "redmi", "oneplus",
                    "motorola", "moto ", "nokia", "realme", "oppo", "vivo", "phone")
PHONE_HEARTBEAT = 2.0  # seconds; the app warns after 5 s of silence

# MiniLab 3 knobs
KNOB_CHANNEL = 0  # MIDI channel 1
KNOB_CCS = (74, 71, 76, 77, 93, 18, 19, 16)
# "absolute" (MiniLab factory setting) or, after switching the knobs to relative in MIDI Control
# Center, the matching relative mode: "relative_offset", "relative_twos" or "relative_signed".
KNOB_MODE = "relative_offset"  # MiniLab knobs set to relative: 65 = one click right, 63 = left
KNOB_SPEED = 2  # macro units (of 127) per knob click
ENCODER_CC = 114        # main encoder turn, relative (64 = center)
ENCODER_CLICK_CC = 115  # main encoder press, 127 down / 0 up

# Bank B pads (notes 44-51 on channel 10) are forwarded to Live's script on their own channel.
# Bank A (notes 36-43, drums) is never forwarded: Live receives it directly from the MiniLab.
PAD_SOURCE_CHANNEL = 9    # MiniLab 3 pads send on MIDI channel 10
PAD_FORWARD_CHANNEL = 12  # MIDI channel 13
BANK_B_NOTES = range(44, 52)

# DualSense
NAV_CHANNEL = 15  # MIDI channel 16, keeps clear of the MiniLab on channel 1
VELOCITY = 100
TRACK_PREV_NOTE = 120   # stick up
TRACK_NEXT_NOTE = 121   # stick down
TRACK_LEFT_NOTE = 125   # stick left
TRACK_RIGHT_NOTE = 126  # stick right
REFRESH_NOTE = 122
COLOR_CCS = (20, 21, 22)  # R, G, B
IDLE_COLOR = (0, 0, 40)   # lightbar until Live reports a track color
LED_BRIGHTNESS = 1.0

LEFT_STICK_X = 0   # left is negative
LEFT_STICK_Y = 1   # up is negative
RIGHT_STICK_Y = 3  # up is negative

# Right stick = selected track volume, as a rate control: while deflected, a CC is sent
# every VOLUME_INTERVAL s (64 = none, 127 = full up, 1 = full down).
VOLUME_CC = 23
VOLUME_DEADZONE = 0.15
VOLUME_INTERVAL = 0.02
TRIGGER = 0.6      # stick past this fires a tap
REARM = 0.3        # stick must return inside this before the next tap

# Xbox controller: transport + scene navigation. Sent on its own channel so it can't
# collide with the DualSense.
XBOX_CHANNEL = 14  # MIDI channel 15
PLAY_NOTE = 0
PAUSE_NOTE = 1
STOP_NOTE = 2
CLIP_LAUNCH_NOTE = 3
SCENE_UP_NOTE = 4
SCENE_DOWN_NOTE = 5
STOP_TRACK_NOTE = 6
STOP_ALL_NOTE = 7
XBOX_LEFT_STICK_Y = 1  # up is negative

# Controllers are told apart by name. Anything else is ignored.
DUALSENSE_NAMES = ("dualsense", "ps5", "playstation")
XBOX_NAMES = ("xbox", "x-box")

# Indices from tools/discover.py; edit if your driver reports different ones.
XBOX_BUTTON_TO_NOTE = {
    2: PLAY_NOTE,          # X
    3: PAUSE_NOTE,         # Y
    0: STOP_NOTE,          # A
    1: CLIP_LAUNCH_NOTE,  # B
    6: STOP_TRACK_NOTE,    # Back / View
    5: STOP_ALL_NOTE,      # RB
}

# DualSense
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

    def note(self, note, on, channel=NAV_CHANNEL):
        self.send(mido.Message(
            "note_on" if on else "note_off",
            note=note, velocity=VELOCITY if on else 0, channel=channel,
        ))

    def tap(self, note, channel=NAV_CHANNEL):
        self.note(note, True, channel)
        self.note(note, False, channel)

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
    """Feedback from Live, on rtmidi's thread: R/G/B CCs for the lightbar (applied by the main
    loop) and SysEx status text (track names, values) for the phone display."""

    def __init__(self, state):
        self._rgb = list(IDLE_COLOR)
        self._state = state
        self.pending = None

    def on_message(self, msg):
        if msg.type == "sysex":
            status = display.parse_status(msg.data)
            if status is not None:
                field, text = status
                self._state.update(**{field: text})
            return
        if msg.type != "control_change" or msg.channel != NAV_CHANNEL or msg.control not in COLOR_CCS:
            return
        self._rgb[COLOR_CCS.index(msg.control)] = min(255, msg.value * 2)
        if msg.control == COLOR_CCS[-1]:  # blue arrives last: color is complete
            self.pending = tuple(self._rgb)
            self._state.update(color="#%02x%02x%02x" % self.pending)


def open_feedback(feedback):
    name = find_port(mido.get_input_names(), FEEDBACK_PORT_PREFIX)
    if name is None:
        print(f"No '{FEEDBACK_PORT_PREFIX}*' port: lightbar stays idle color. "
              "Create it in loopMIDI to enable track-color feedback.")
        return None
    print(f"Feedback: {name}")
    return mido.open_input(name, callback=feedback.on_message)


class PhoneLink(threading.Thread):
    """Sends the display state to the phone app as SysEx on every change and every
    PHONE_HEARTBEAT seconds. Finds the phone's port by name and reconnects after a replug."""

    def __init__(self, state):
        super().__init__(daemon=True)
        self._state = state
        self._port = None
        self._next_scan = 0.0
        self._announced_search = False

    def _find_port(self):
        for name in mido.get_output_names():
            if any(hint in name.lower() for hint in PHONE_PORT_HINTS):
                return name
        return None

    def run(self):
        version, last_sent = -1, 0.0
        while True:
            if self._port is None and time.monotonic() >= self._next_scan:
                self._next_scan = time.monotonic() + 3
                name = self._find_port()
                if name is not None:
                    try:
                        self._port = mido.open_output(name)
                        print(f"Phone: {name}")
                        self._state.set_controller("phone", True)
                        last_sent = 0.0  # send right away
                    except (IOError, OSError) as exc:
                        print(f"Phone port {name} found but not opened: {exc}")
                elif not self._announced_search:
                    self._announced_search = True
                    print("Phone app: not connected (set the phone's USB mode to MIDI). Output ports:",
                          mido.get_output_names())

            new_version, snapshot = self._state.wait(version, 1.0)
            due = new_version != version or time.monotonic() - last_sent >= PHONE_HEARTBEAT
            version = new_version
            if self._port is not None and due:
                try:
                    # Windows may accept writes to an unplugged device silently: check it still exists
                    if self._port.name not in mido.get_output_names():
                        raise IOError("port gone")
                    self._port.send(mido.Message("sysex", data=display.snapshot_sysex(snapshot)))
                    last_sent = time.monotonic()
                except (IOError, OSError, ValueError):
                    print("Phone disconnected. Waiting...")
                    self._port.close()
                    self._port = None
                    self._announced_search = False
                    self._state.set_controller("phone", False)


def open_minilab(out, state):
    name = find_port(mido.get_input_names(), MINILAB_PORT_PREFIX)
    if name is None:
        print(f"MiniLab not found (looked for '{MINILAB_PORT_PREFIX}*'). "
              "Running with DualSense only.")
        return None

    knob_index = {cc: i for i, cc in enumerate(KNOB_CCS)}
    decoder = knobs.KnobDecoder(KNOB_MODE)
    pages = knobs.KnobPages()

    def send(messages):
        for kind, number, *value in messages:
            if kind == "cc":
                out.send(mido.Message("control_change", channel=knobs.MACRO_CHANNEL,
                                      control=number, value=value[0]))
            else:
                out.tap(number, knobs.MACRO_CHANNEL)

    def show_page():
        param = "Volume" if pages.mix_param == knobs.VOLUME else f"Macro {pages.mix_param}"
        state.update(page=pages.page, mix_param=param)

    def forward(msg):
        if msg.type == "control_change" and msg.channel == KNOB_CHANNEL:
            if msg.control in knob_index:
                i = knob_index[msg.control]
                send(pages.knob(i, decoder.step(i, msg.value) * KNOB_SPEED))
            elif msg.control == ENCODER_CC:
                send(pages.turn(knobs.decode_relative(msg.value, "relative_offset")))
                show_page()
            elif msg.control == ENCODER_CLICK_CC and msg.value > 0:
                send(pages.press())
                show_page()
                print(f"Knob page: {pages.page}")
        elif (msg.type in ("note_on", "note_off") and msg.channel == PAD_SOURCE_CHANNEL
              and msg.note in BANK_B_NOTES):
            out.send(msg.copy(channel=PAD_FORWARD_CHANNEL))

    # The bridge always starts on DEVICE: clear any pin Live's script kept from a previous run
    send([("note", knobs.UNPIN_NOTE)])
    port = mido.open_input(name, callback=forward)
    print(f"MiniLab: {name}")
    state.set_controller("MiniLab", True)
    return port


def classify(name):
    lowered = name.lower()
    if any(key in lowered for key in DUALSENSE_NAMES):
        return "dualsense"
    if any(key in lowered for key in XBOX_NAMES):
        return "xbox"
    return None


class DualSenseState:
    def __init__(self):
        self.left_x = 0.0
        self.left_y = 0.0
        self.right_y = 0.0
        self.last_volume_send = 0.0
        self.stick_ready = True
        self.last_hat = (0, 0)


def handle_dualsense(event, state, out):
    if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
        note = BUTTON_TO_NOTE.get(event.button)
        if note is not None:
            out.note(note, event.type == pygame.JOYBUTTONDOWN)

    elif event.type == pygame.JOYHATMOTION:
        old_note = HAT_TO_NOTE.get(state.last_hat)
        new_note = HAT_TO_NOTE.get(event.value)
        if old_note is not None:
            out.note(old_note, False)
        if new_note is not None:
            out.note(new_note, True)
        state.last_hat = event.value

    elif event.type == pygame.JOYAXISMOTION and event.axis == RIGHT_STICK_Y:
        state.right_y = event.value

    elif event.type == pygame.JOYAXISMOTION and event.axis in (LEFT_STICK_X, LEFT_STICK_Y):
        # One step per flick; the stick must return to center before the next one.
        # Vertical and horizontal flicks are sent separately: Live's script picks the one
        # that applies to the visible view (arrangement = vertical, session = horizontal).
        if event.axis == LEFT_STICK_X:
            state.left_x = event.value
        else:
            state.left_y = event.value
        strongest = max(abs(state.left_x), abs(state.left_y))
        if strongest < REARM:
            state.stick_ready = True
        elif state.stick_ready and strongest > TRIGGER:
            state.stick_ready = False
            if abs(state.left_x) > abs(state.left_y):  # dominant axis wins on diagonals
                note, label = (TRACK_LEFT_NOTE, "left") if state.left_x < 0 else (TRACK_RIGHT_NOTE, "right")
            else:
                note, label = (TRACK_PREV_NOTE, "up") if state.left_y < 0 else (TRACK_NEXT_NOTE, "down")
            out.tap(note)
            print(f"Stick {label}")


class XboxState:
    def __init__(self):
        self.stick_ready = True


def handle_xbox(event, state, out):
    if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
        note = XBOX_BUTTON_TO_NOTE.get(event.button)
        if note is not None:
            out.note(note, event.type == pygame.JOYBUTTONDOWN, XBOX_CHANNEL)

    elif event.type == pygame.JOYAXISMOTION and event.axis == XBOX_LEFT_STICK_Y:
        # One scene step per flick, same behaviour as the DualSense track stick
        value = event.value
        if abs(value) < REARM:
            state.stick_ready = True
        elif state.stick_ready and abs(value) > TRIGGER:
            state.stick_ready = False
            if value < 0:
                out.tap(SCENE_UP_NOTE, XBOX_CHANNEL)
                print("Scene up")
            else:
                out.tap(SCENE_DOWN_NOTE, XBOX_CHANNEL)
                print("Scene down")


def send_volume(state, out, now):
    if abs(state.right_y) <= VOLUME_DEADZONE or now - state.last_volume_send < VOLUME_INTERVAL:
        return
    state.last_volume_send = now
    # 0 at the edge of the deadzone, 1 at full deflection; up (negative) raises
    amount = (abs(state.right_y) - VOLUME_DEADZONE) / (1.0 - VOLUME_DEADZONE)
    direction = -1 if state.right_y > 0 else 1
    value = 64 + direction * round(63 * min(1.0, amount))
    out.send(mido.Message("control_change", control=VOLUME_CC, value=value, channel=NAV_CHANNEL))


def run(out, lightbar, feedback, state):
    joysticks = {}   # instance_id -> Joystick (must stay referenced or SDL closes it)
    kinds = {}       # instance_id -> "dualsense" | "xbox"
    dualsense = DualSenseState()
    xbox = XboxState()

    print("Waiting for controllers (DualSense, Xbox)...")

    while True:
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEADDED:
                js = pygame.joystick.Joystick(event.device_index)
                kind = classify(js.get_name())
                if kind is None or kind in kinds.values():
                    print(f"Ignoring controller: {js.get_name()}")
                    continue
                joysticks[js.get_instance_id()] = js
                kinds[js.get_instance_id()] = kind
                print(f"{kind}: {js.get_name()}")
                state.set_controller(kind, True)
                if kind == "dualsense":
                    lightbar.attach(event.device_index)
                    lightbar.set(IDLE_COLOR)
                    out.tap(REFRESH_NOTE)  # ask Live for the current track color

            elif event.type == pygame.JOYDEVICEREMOVED:
                kind = kinds.pop(event.instance_id, None)
                joysticks.pop(event.instance_id, None)
                if kind == "dualsense":
                    dualsense = DualSenseState()
                    lightbar.detach()
                elif kind == "xbox":
                    xbox = XboxState()
                if kind is not None:
                    print(f"{kind} disconnected. Waiting...")
                    state.set_controller(kind, False)

            elif hasattr(event, "instance_id"):
                kind = kinds.get(event.instance_id)
                if kind == "dualsense":
                    handle_dualsense(event, dualsense, out)
                elif kind == "xbox":
                    handle_xbox(event, xbox, out)

        send_volume(dualsense, out, time.monotonic())

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

    state = display.DisplayState()
    server = display.start_server(state)
    for address in display.lan_addresses() or ["<this-pc-ip>"]:
        print(f"Display: http://{address}:{display.PORT}")

    PhoneLink(state).start()
    minilab = open_minilab(out, state)

    pygame.init()
    pygame.joystick.init()
    lightbar = Lightbar()
    feedback = ColorFeedback(state)
    feedback_port = open_feedback(feedback)
    print("Bridge running. Ctrl+C to stop.")
    try:
        run(out, lightbar, feedback, state)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        for port in (minilab, feedback_port):
            if port is not None:
                port.close()
        out.close()
        server.shutdown()
        pygame.quit()


if __name__ == "__main__":
    main()
