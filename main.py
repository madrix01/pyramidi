import time
import pygame
import mido

MIDI_PORT = "ps5_midi 6"
DEADZONE = 0.08

# ---------- CC Mapping ----------
AXIS_CC = {
    0: 20,  # Left X
    1: 21,  # Left Y
    2: 22,  # Right X
    3: 23,  # Right Y
    4: 24,  # L2
    5: 25,  # R2
}

# ---------- Button Mapping ----------
BUTTON_NOTE = {
    4: 64,   # L1
    5: 65,   # R1
    6: 66,   # L2 Button (if present)
    7: 67,   # R2 Button (if present)
    8: 68,   # Create
    9: 69,   # Options
    10: 70,  # L3
    11: 71,  # R3
    12: 72,  # PS
    13: 73,  # Touchpad Click
    14: 74,  # Mute (if exposed)
}

BUTTON_CC = {
    0: 100,  # Cross
    1: 101,  # Circle
    2: 102,  # Square
    3: 103,  # Triangle
    4: 104,  # L1
    5: 105,  # R1
    6: 106,  # Create
    7: 107,  # Options
    8: 108,  # L3
    9: 109,  # R3
    10: 110, # PS
    11: 111, # Touchpad
}

DPAD_NOTES = {
    (0, 1): 80,     # Up
    (0, -1): 81,    # Down
    (-1, 0): 82,    # Left
    (1, 0): 83,     # Right
}

pygame.init()
pygame.joystick.init()

if pygame.joystick.get_count() == 0:
    raise RuntimeError("No controller found")

js = pygame.joystick.Joystick(0)
js.init()
print("Output Ports:")
for p in mido.get_output_names():
    print(repr(p))

print("Connected:", js.get_name())

port = mido.open_output(MIDI_PORT)

last_axis = {}
last_button = {}
last_hat = (0, 0)


def axis_to_midi(v):
    if abs(v) < DEADZONE:
        v = 0.0

    value = int((v + 1.0) * 63.5)
    return max(0, min(127, value))




while True:

    pygame.event.pump()

    # ---------------- AXES ----------------

    for axis in range(js.get_numaxes()):

        value = axis_to_midi(js.get_axis(axis))

        if last_axis.get(axis) == value:
            continue

        last_axis[axis] = value

        if axis in AXIS_CC:
            port.send(
                mido.Message(
                    "control_change",
                    control=AXIS_CC[axis],
                    value=value
                )
            )

    # ---------------- BUTTONS ----------------

    for button in range(js.get_numbuttons()):

        pressed = js.get_button(button)

        if last_button.get(button) == pressed:
            continue

        last_button[button] = pressed

        if button not in BUTTON_NOTE:
            continue

        note = BUTTON_NOTE[button]

        if pressed:
            port.send(
                mido.Message(
                    "note_on",
                    note=note,
                    velocity=127
                )
            )
            print(f"Button {button} -> Note {note} ON")

        else:
            port.send(
                mido.Message(
                    "note_off",
                    note=note,
                    velocity=0
                )
            )

    # ---------------- DPAD ----------------

    if js.get_numhats() > 0:
        hat = js.get_hat(0)
        print(">>>", hat)

        if hat != last_hat:

            if last_hat in DPAD_NOTES:
                port.send(
                    mido.Message(
                        "note_off",
                        note=DPAD_NOTES[last_hat],
                        velocity=0,
                    )
                )

            if hat in DPAD_NOTES:
                port.send(
                    mido.Message(
                        "note_on",
                        note=DPAD_NOTES[hat],
                        velocity=127,
                    )
                )

            last_hat = hat

    time.sleep(0.005)