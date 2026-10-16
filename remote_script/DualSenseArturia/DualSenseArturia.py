"""
Live 12 control surface: DualSense track navigation + MiniLab 3 knobs on rack macros.

Input port: the loopMIDI port fed by bridge.py. Keep the constants below in sync
with bridge.py (they run in different Python runtimes and can't share a module).

Behaviour:
- Nav notes from the DualSense select the previous/next visible track. Selection
  only; transport and clips are never touched.
- Whenever the selected track changes (joystick, mouse, anything), the 8 knobs are
  tracked against Macro 1-8 of the first rack on that track, with pickup: a knob is
  ignored until it crosses the macro's current value, then it controls it.
"""
import Live
from _Framework.ButtonElement import ButtonElement
from _Framework.ControlSurface import ControlSurface
from _Framework.EncoderElement import EncoderElement
from _Framework.InputControlElement import MIDI_CC_TYPE, MIDI_NOTE_TYPE

# MiniLab 3 knobs (forwarded unchanged by bridge.py)
KNOB_CHANNEL = 0  # MIDI channel 1
KNOB_CCS = (74, 71, 76, 77, 93, 18, 19, 16)
# Knobs must send absolute 0-127 values. After a track change a knob only takes over
# its macro once it reaches the macro's current value (pickup), so sounds never jump.
PICKUP_TOLERANCE = 2  # knob units (0-127) counted as "reached the macro"

# DualSense navigation (sent by bridge.py)
NAV_CHANNEL = 15  # MIDI channel 16
TRACK_PREV_NOTE = 120
TRACK_NEXT_NOTE = 121
REFRESH_NOTE = 122  # bridge sends this on start-up so the lightbar syncs

# Lightbar feedback: selected track's color as 3 CCs (7-bit each) on NAV_CHANNEL.
# Needs the feedback port set as this script's Output in Live.
COLOR_CCS = (20, 21, 22)  # R, G, B

# Right stick -> selected track volume. Messages arrive ~50/s while the stick is held:
# 64 = no movement, 127 = full up, 1 = full down. Volume spans 0.0-1.0 (0.85 = 0 dB).
VOLUME_CC = 23
VOLUME_FULL_STEP = 0.005  # change per message at full deflection (~3.4 s for 0 to 0 dB)

# Skip tracks that have no rack when navigating with the joystick
SKIP_TRACKS_WITHOUT_RACK = False


def find_rack(track):
    for device in track.devices:
        if device.can_have_chains:
            return device
    return None


class DualSenseArturia(ControlSurface):
    def __init__(self, c_instance):
        super().__init__(c_instance)
        self._watched_track = None
        self._macros = [None] * len(KNOB_CCS)
        self._knob_pos = [None] * len(KNOB_CCS)   # last CC value seen per knob
        self._picked = [False] * len(KNOB_CCS)
        self._last_set = [None] * len(KNOB_CCS)   # last value we wrote, in knob units
        with self.component_guard():
            self._create_controls()
        self.song().view.add_selected_track_listener(self._dsa_selected_track_changed)
        self._dsa_selected_track_changed()
        self.log_message("DualSenseArturia loaded")
        self.show_message("DualSenseArturia loaded")

    def _create_controls(self):
        self._encoders = [
            EncoderElement(MIDI_CC_TYPE, KNOB_CHANNEL, cc, Live.MidiMap.MapMode.absolute)
            for cc in KNOB_CCS
        ]
        # Not mapped to a parameter: the script receives the raw CC and applies pickup itself
        self._knob_callbacks = []
        for i, encoder in enumerate(self._encoders):
            callback = lambda value, i=i: self._dsa_knob(i, value)
            self._knob_callbacks.append(callback)
            encoder.add_value_listener(callback)
        self._volume_encoder = EncoderElement(
            MIDI_CC_TYPE, NAV_CHANNEL, VOLUME_CC, Live.MidiMap.MapMode.absolute
        )
        self._volume_encoder.add_value_listener(self._dsa_volume)
        self._prev_button = ButtonElement(True, MIDI_NOTE_TYPE, NAV_CHANNEL, TRACK_PREV_NOTE)
        self._next_button = ButtonElement(True, MIDI_NOTE_TYPE, NAV_CHANNEL, TRACK_NEXT_NOTE)
        self._prev_button.add_value_listener(self._dsa_prev)
        self._next_button.add_value_listener(self._dsa_next)
        self._refresh_button = ButtonElement(True, MIDI_NOTE_TYPE, NAV_CHANNEL, REFRESH_NOTE)
        self._refresh_button.add_value_listener(self._dsa_refresh)

    # ---------- navigation ----------

    def _dsa_prev(self, value):
        if value:
            self._move_track(-1)

    def _dsa_next(self, value):
        if value:
            self._move_track(1)

    def _dsa_volume(self, value):
        amount = (value - 64) / 63.0
        amount *= abs(amount)  # squared response: fine control near the center
        volume = self.song().view.selected_track.mixer_device.volume
        volume.value = max(volume.min, min(volume.max, volume.value + amount * VOLUME_FULL_STEP))

    def _move_track(self, direction):
        song = self.song()
        tracks = list(song.visible_tracks)
        if not tracks:
            return
        try:
            index = tracks.index(song.view.selected_track)
        except ValueError:
            # master/return selected: jump to the nearest end
            index = len(tracks) if direction < 0 else -1

        new_index = index + direction
        while 0 <= new_index < len(tracks):
            if not SKIP_TRACKS_WITHOUT_RACK or find_rack(tracks[new_index]):
                song.view.selected_track = tracks[new_index]
                return
            new_index += direction
        self.show_message("End of track list")

    # ---------- knob binding ----------

    def _dsa_selected_track_changed(self):
        track = self.song().view.selected_track
        self._watch_devices(track)
        self._bind_knobs(track)
        self._send_color(track)

    def _dsa_refresh(self, value):
        if value:
            self._send_color(self.song().view.selected_track)

    def _send_color(self, track):
        # track.color is 0xRRGGBB; send 7-bit channels, bridge scales back to 8-bit
        color = track.color
        for cc, shift in zip(COLOR_CCS, (16, 8, 0)):
            self._send_midi((0xB0 | NAV_CHANNEL, cc, ((color >> shift) & 0xFF) >> 1))

    def _dsa_color_changed(self):
        self._send_color(self.song().view.selected_track)

    def _watch_devices(self, track):
        # Rebind if a rack is added/removed/reordered on the current track,
        # and resend the lightbar color if the track is recolored
        self._unwatch_devices()
        if hasattr(track, "add_devices_listener"):
            track.add_devices_listener(self._dsa_devices_changed)
            track.add_color_listener(self._dsa_color_changed)
            self._watched_track = track

    def _unwatch_devices(self):
        old = self._watched_track
        self._watched_track = None
        try:
            if old is not None and old.devices_has_listener(self._dsa_devices_changed):
                old.remove_devices_listener(self._dsa_devices_changed)
            if old is not None and old.color_has_listener(self._dsa_color_changed):
                old.remove_color_listener(self._dsa_color_changed)
        except RuntimeError:
            pass  # track was deleted; Live already dropped its listeners

    def _dsa_devices_changed(self):
        self._bind_knobs(self.song().view.selected_track)

    def _bind_knobs(self, track):
        rack = find_rack(track) if hasattr(track, "devices") else None
        # parameters[0] is "Device On"; 1..16 are the macros
        params = list(rack.parameters) if rack is not None else []
        for i in range(len(KNOB_CCS)):
            self._macros[i] = params[i + 1] if i + 1 < len(params) else None
            self._picked[i] = False
            self._last_set[i] = None

        if rack is None:
            self.show_message("%s: no rack, knobs unassigned" % track.name)
        else:
            # Show the controlled rack in the Device view (selection only, no playback effect)
            self.song().view.select_device(rack)
            self.show_message("%s  |  %s" % (track.name, rack.name))

    def _dsa_knob(self, i, value):
        previous, self._knob_pos[i] = self._knob_pos[i], value
        param = self._macros[i]
        if param is None:
            return
        span = param.max - param.min
        current = (param.value - param.min) / span * 127.0

        # Macro changed elsewhere (automation, mouse): require pickup again
        if self._picked[i] and abs(current - self._last_set[i]) > PICKUP_TOLERANCE:
            self._picked[i] = False

        if not self._picked[i]:
            crossed = previous is not None and (previous - current) * (value - current) <= 0
            if abs(value - current) > PICKUP_TOLERANCE and not crossed:
                return
            self._picked[i] = True

        param.value = param.min + value / 127.0 * span
        self._last_set[i] = value
        self.show_message("%s  |  %s: %s" % (
            self.song().view.selected_track.name, param.name, param.str_for_value(param.value)))

    def disconnect(self):
        view = self.song().view
        if view.selected_track_has_listener(self._dsa_selected_track_changed):
            view.remove_selected_track_listener(self._dsa_selected_track_changed)
        self._unwatch_devices()
        for encoder, callback in zip(self._encoders, self._knob_callbacks):
            encoder.remove_value_listener(callback)
        self._volume_encoder.remove_value_listener(self._dsa_volume)
        self._prev_button.remove_value_listener(self._dsa_prev)
        self._next_button.remove_value_listener(self._dsa_next)
        self._refresh_button.remove_value_listener(self._dsa_refresh)
        super().disconnect()
