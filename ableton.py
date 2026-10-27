"""
PS5 DualSense -> loopMIDI -> Ableton Live clip trigger script.

Sends MIDI note on/off through a virtual MIDI port (loopMIDI on Windows,
IAC Driver on Mac) whenever a controller button or D-pad direction is pressed.

Requirements:
    pip install pygame mido python-rtmidi

Setup:
    1. Create a virtual MIDI port named "PS5-Ableton" (loopMIDI / IAC Driver)
    2. In Ableton: Preferences > Link/Tempo/MIDI > set that port's Remote column to On
    3. Run this script, then Ctrl/Cmd+M in Ableton and click each clip to map a button
"""

import time
import pygame
import mido

PORT_NAME = "ps5_midi 6"   # must match your virtual MIDI port name
MIDI_CHANNEL = 0            # channel 1 in Ableton's UI
VELOCITY = 100

# Face/shoulder/stick buttons -> MIDI note. Indices are for pygame's SDL2
# backend; they can vary slightly by OS/driver. Run discover_buttons() below
# once to confirm indices on your machine, then edit this table.
BUTTON_TO_NOTE = {
    0: 0,   # Cross / X
    1: 1,   # Circle
    2: 2,   # Square
    3: 3,   # Triangle
    4: 64,   # L1
    5: 65,   # R1
    6: 66,   # L2 click (digital press, not analog)
    7: 67,   # R2 click
    9: 68,   # L3 (left stick click)
    10: 69,  # R3 (right stick click)
}

# D-pad comes through as a "hat", not a button, on most drivers.
HAT_TO_NOTE = {
    (0, 1): 70,    # Up
    (0, -1): 71,   # Down
    (-1, 0): 72,   # Left
    (1, 0): 73,    # Right
}

LEFT_STICK_Y = 1      # Left joystick vertical axis
DEADZONE = 0.4

SCENE_UP_NOTE = 70
SCENE_DOWN_NOTE = 71


def main():
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("No controller detected. Connect the DualSense via USB or Bluetooth and retry.")
        return

    js = pygame.joystick.Joystick(0)
    js.init()
    print(f"Connected: {js.get_name()}")

    try:
        outport = mido.open_output(PORT_NAME)
    except IOError:
        print(f"Could not open MIDI port '{PORT_NAME}'.")
        print("Available ports:", mido.get_output_names())
        return

    print(f"Sending MIDI to '{PORT_NAME}'. Press Ctrl+C to quit.")

    last_hat_note = None

    stick_ready = True
    try:
        while True:
            for event in pygame.event.get():
                if event.type == pygame.JOYBUTTONDOWN:
                    note = BUTTON_TO_NOTE.get(event.button)
                    if note is not None:
                        outport.send(mido.Message("note_on", note=note, velocity=VELOCITY, channel=MIDI_CHANNEL))
                        print(f"button {event.button} -> note_on {note}")

                elif event.type == pygame.JOYBUTTONUP:
                    note = BUTTON_TO_NOTE.get(event.button)
                    if note is not None:
                        outport.send(mido.Message("note_off", note=note, velocity=0, channel=MIDI_CHANNEL))

                elif event.type == pygame.JOYHATMOTION:
                    # release whatever hat note was previously held
                    if last_hat_note is not None:
                        outport.send(mido.Message("note_off", note=last_hat_note, velocity=0, channel=MIDI_CHANNEL))
                        last_hat_note = None

                    note = HAT_TO_NOTE.get(event.value)
                    if note is not None:
                        outport.send(mido.Message("note_on", note=note, velocity=VELOCITY, channel=MIDI_CHANNEL))
                        print(f"dpad {event.value} -> note_on {note}")
                        last_hat_note = note
                elif event.type == pygame.JOYAXISMOTION:
                    if event.axis == LEFT_STICK_Y:

                        value = event.value

                        # Stick returned to center -> ready for next navigation
                        if abs(value) < DEADZONE:
                            stick_ready = True

                        elif stick_ready:
                            if value < -DEADZONE:
                                # Scene Up
                                outport.send(
                                    mido.Message(
                                        "note_on",
                                        note=SCENE_UP_NOTE,
                                        velocity=VELOCITY,
                                        channel=MIDI_CHANNEL,
                                    )
                                )
                                outport.send(
                                    mido.Message(
                                        "note_off",
                                        note=SCENE_UP_NOTE,
                                        velocity=0,
                                        channel=MIDI_CHANNEL,
                                    )
                                )
                                print("Joystick UP -> Scene Up")
                                stick_ready = False

                            elif value > DEADZONE:
                                # Scene Down
                                outport.send(
                                    mido.Message(
                                        "note_on",
                                        note=SCENE_DOWN_NOTE,
                                        velocity=VELOCITY,
                                        channel=MIDI_CHANNEL,
                                    )
                                )
                                outport.send(
                                    mido.Message(
                                        "note_off",
                                        note=SCENE_DOWN_NOTE,
                                        velocity=0,
                                        channel=MIDI_CHANNEL,
                                    )
                                )
                                print("Joystick DOWN -> Scene Down")
                                stick_ready = False
            time.sleep(0.005)

    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        outport.close()


def discover_buttons():
    """Run this instead of main() to print raw button/hat indices for your controller."""
    pygame.init()
    pygame.joystick.init()
    js = pygame.joystick.Joystick(0)
    js.init()
    print(f"Connected: {js.get_name()}. Press buttons / move D-pad. Ctrl+C to quit.")
    try:
        while True:
            for event in pygame.event.get():
                if event.type == pygame.JOYBUTTONDOWN:
                    print(f"button index: {event.button}")
                elif event.type == pygame.JOYHATMOTION:
                    print(f"hat value: {event.value}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
    # discover_buttons()  # uncomment this and comment out main() to find your button indices