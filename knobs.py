"""
MiniLab 3 knob pages. Pure logic, no MIDI ports: bridge.py feeds it raw knob/encoder values
and sends whatever it returns to Live's script. Tested by tests/test_knobs.py.

Knobs never set absolute values. Every knob move becomes a step (+n / -n) that the Live script
adds to the target parameter's current value, so nothing jumps when the target changes and one
knob can move several parameters that sit at different values.

Pages (main encoder press cycles them):
  device  knobs 1-8 -> Macro 1-8 of the selected track
  split   knobs 1-4 -> Macro 1-4 of the selected track, knobs 5-8 -> Macro 1-4 of the pinned track
  link    knob N    -> Macro N of the selected AND the pinned track
  mix     knob N    -> one parameter (volume or Macro 1-8) of track N, counting from the selected
                       track. Main encoder turn picks the parameter.

Entering split pins the track selected at that moment; returning to device unpins it.

Wire format (Live script decodes the same numbers, keep in sync):
  CC on MACRO_CHANNEL, value 64 + step:
      SELECTED_BASE + p   selected track, p = 0 volume, 1-8 macro
      PINNED_BASE + p     pinned track
      MIX_BASE + k*MIX_STRIDE + p   track k places right of the selected one (k = 0-7)
  Notes on MACRO_CHANNEL: PIN_NOTE, UNPIN_NOTE, PAGE_NOTE_BASE + page index,
      MIX_PARAM_NOTE_BASE + p (status messages only)
"""

MACRO_CHANNEL = 10  # MIDI channel 11
SELECTED_BASE = 0
PINNED_BASE = 16
MIX_BASE = 32
MIX_STRIDE = 10
PIN_NOTE = 1
UNPIN_NOTE = 2
PAGE_NOTE_BASE = 10
MIX_PARAM_NOTE_BASE = 20

PAGES = ("device", "split", "link", "mix")
VOLUME = 0  # parameter index 0 = track volume, 1-8 = macros
MAX_STEP = 16  # guard against a jump in absolute mode being treated as a huge turn
ENCODER_STEP = 8  # summed main-encoder steps per mix-parameter change


def decode_relative(value, mode):
    """Step size from one relative CC value. Returns 0 for 'no movement'."""
    if mode == "relative_offset":  # 64 = none, 65+ up, 63- down (MiniLab main encoder)
        return value - 64
    if mode == "relative_twos":  # 1-63 up, 127-65 down
        return value if value < 64 else value - 128
    if mode == "relative_signed":  # 1-63 up, 65-127 down
        return value if value < 64 else -(value - 64)
    raise ValueError("unknown relative mode: %s" % mode)


class KnobDecoder:
    """Turns raw knob CC values into steps.

    absolute: steps are the difference to the previous value of the same knob. The first value
    seen after start-up only sets the reference. Works with the MiniLab's factory setting, but a
    knob stops sending at 0 and 127, so relative mode is better.
    """

    def __init__(self, mode, count=8):
        self.mode = mode
        self._last = [None] * count

    def step(self, knob, value):
        if self.mode != "absolute":
            return decode_relative(value, self.mode)
        previous, self._last[knob] = self._last[knob], value
        if previous is None:
            return 0
        return max(-MAX_STEP, min(MAX_STEP, value - previous))


class KnobPages:
    def __init__(self):
        self.page = "device"
        self.mix_param = VOLUME
        self._encoder_accum = 0

    # Each method returns a list of ("cc", number, value) / ("note", number) messages.

    def knob(self, knob, step):
        if step == 0:
            return []
        value = 64 + max(-63, min(63, step))
        return [("cc", cc, value) for cc in self._targets(knob)]

    def _targets(self, knob):
        macro = knob + 1
        if self.page == "device":
            return [SELECTED_BASE + macro]
        if self.page == "split":
            if knob < 4:
                return [SELECTED_BASE + macro]
            return [PINNED_BASE + macro - 4]
        if self.page == "link":
            return [SELECTED_BASE + macro, PINNED_BASE + macro]
        return [MIX_BASE + knob * MIX_STRIDE + self.mix_param]

    def press(self):
        """Main encoder press: next page."""
        old = self.page
        self.page = PAGES[(PAGES.index(old) + 1) % len(PAGES)]
        messages = []
        if self.page == "split":
            messages.append(("note", PIN_NOTE))  # pin the track selected right now
        elif self.page == "device":
            messages.append(("note", UNPIN_NOTE))
        messages.append(("note", PAGE_NOTE_BASE + PAGES.index(self.page)))
        return messages

    def turn(self, step):
        """Main encoder turn: picks the mix parameter (volume, Macro 1-8). Ignored on other pages."""
        if self.page != "mix":
            return []
        self._encoder_accum += step
        if abs(self._encoder_accum) < ENCODER_STEP:
            return []
        direction = 1 if self._encoder_accum > 0 else -1
        self._encoder_accum = 0
        new = max(VOLUME, min(8, self.mix_param + direction))
        if new == self.mix_param:
            return []
        self.mix_param = new
        return [("note", MIX_PARAM_NOTE_BASE + new)]
