"""
Live 12 control surface: DualSense track navigation + MiniLab 3 knobs on rack macros.

Input port: the loopMIDI port fed by bridge.py. Keep the constants below in sync
with bridge.py (they run in different Python runtimes and can't share a module).

Behaviour:
- Nav notes from the DualSense select the previous/next visible track. Selection
  only; transport and clips are never touched.
- MiniLab knobs arrive as steps (+n / -n) addressed to the selected track, the pinned track
  or a track to the right of the selected one (bridge.py / knobs.py decide which, per knob
  page). Each step is added to the parameter's current value, so nothing ever jumps.
"""
import Live
from _Framework.ButtonElement import ButtonElement
from _Framework.ControlSurface import ControlSurface
from _Framework.EncoderElement import EncoderElement
from _Framework.InputControlElement import MIDI_CC_TYPE, MIDI_NOTE_TYPE

# MiniLab knob steps from bridge.py. Same numbers as knobs.py (separate runtime, can't import it).
# CC value is 64 + step. Parameter p: 0 = track volume, 1-8 = Macro 1-8 of the first rack.
MACRO_CHANNEL = 10  # MIDI channel 11
SELECTED_BASE = 0   # CC SELECTED_BASE + p: selected track
PINNED_BASE = 16    # CC PINNED_BASE + p: pinned track
MIX_BASE = 32       # CC MIX_BASE + k * MIX_STRIDE + p: track k places right of the selected one
MIX_STRIDE = 10
MIX_TRACKS = 8
PARAMS = 9  # volume + 8 macros
PIN_NOTE = 1        # pin the selected track
UNPIN_NOTE = 2
PAGE_NOTE_BASE = 10  # + page index, status message only
PAGE_NAMES = ("DEVICE", "SPLIT", "LINK", "MIX")
MIX_PARAM_NOTE_BASE = 20  # + p, status message only
KNOB_STEP = 1.0 / 127  # one step = 1/127 of a parameter's range

# DualSense navigation (sent by bridge.py)
NAV_CHANNEL = 15  # MIDI channel 16
# Left stick, arrangement view: up/down change track.
# Left stick, session view: left/right change track, up/down select the clip on the track.
TRACK_PREV_NOTE = 120   # stick up
TRACK_NEXT_NOTE = 121   # stick down
TRACK_LEFT_NOTE = 125   # stick left
TRACK_RIGHT_NOTE = 126  # stick right
REFRESH_NOTE = 122  # bridge sends this on start-up so the lightbar syncs


# Xbox controller: transport + scene navigation (sent by bridge.py)
XBOX_CHANNEL = 14  # MIDI channel 15
PLAY_NOTE = 0
PAUSE_NOTE = 1
STOP_NOTE = 2
CLIP_LAUNCH_NOTE = 3
SCENE_UP_NOTE = 4
SCENE_DOWN_NOTE = 5
STOP_TRACK_NOTE = 6
STOP_ALL_NOTE = 7

# MiniLab 3 Bank B pads (forwarded by bridge.py from the MiniLab's channel 10, notes 44-51)
PAD_CHANNEL = 12  # MIDI channel 13
PAD_TOGGLE_NOTE = 44  # pad 1: toggle the selected track's instrument on/off
PAD_HOLD_NOTE = 45    # pad 2: instrument on while held, off on release

# What pad 1 switches: "device" = the rack's Device On (instrument only, effects after it
# keep ringing out) or "track" = track mute (silences the whole track).
TOGGLE_TARGET = "device"

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
        self._held_device_on = None  # Device On parameter switched on by pad 2
        self._pinned = None  # track pinned by the split page
        with self.component_guard():
            self._create_controls()
        self.song().view.add_selected_track_listener(self._dsa_selected_track_changed)
        self._dsa_selected_track_changed()
        self.log_message("DualSenseArturia loaded")
        self.show_message("DualSenseArturia loaded")

    def _create_controls(self):
        # Knob steps and page messages: (element, callback) pairs, kept for disconnect()
        self._knob_listeners = []
        targets = [(SELECTED_BASE, "selected"), (PINNED_BASE, "pinned")]
        targets += [(MIX_BASE + k * MIX_STRIDE, k) for k in range(MIX_TRACKS)]
        for base, target in targets:
            for p in range(PARAMS):
                encoder = EncoderElement(
                    MIDI_CC_TYPE, MACRO_CHANNEL, base + p, Live.MidiMap.MapMode.absolute
                )
                callback = lambda value, target=target, p=p: self._dsa_step(target, p, value)
                encoder.add_value_listener(callback)
                self._knob_listeners.append((encoder, callback))
        notes = [(PIN_NOTE, self._dsa_pin), (UNPIN_NOTE, self._dsa_unpin)]
        notes += [(PAGE_NOTE_BASE + i, lambda value, i=i: self._dsa_page(i, value))
                  for i in range(len(PAGE_NAMES))]
        notes += [(MIX_PARAM_NOTE_BASE + p, lambda value, p=p: self._dsa_mix_param(p, value))
                  for p in range(PARAMS)]
        for note, callback in notes:
            button = ButtonElement(True, MIDI_NOTE_TYPE, MACRO_CHANNEL, note)
            button.add_value_listener(callback)
            self._knob_listeners.append((button, callback))
        self._volume_encoder = EncoderElement(
            MIDI_CC_TYPE, NAV_CHANNEL, VOLUME_CC, Live.MidiMap.MapMode.absolute
        )
        self._volume_encoder.add_value_listener(self._dsa_volume)
        self._prev_button = ButtonElement(True, MIDI_NOTE_TYPE, NAV_CHANNEL, TRACK_PREV_NOTE)
        self._next_button = ButtonElement(True, MIDI_NOTE_TYPE, NAV_CHANNEL, TRACK_NEXT_NOTE)
        self._prev_button.add_value_listener(self._dsa_prev)
        self._next_button.add_value_listener(self._dsa_next)
        self._left_button = ButtonElement(True, MIDI_NOTE_TYPE, NAV_CHANNEL, TRACK_LEFT_NOTE)
        self._right_button = ButtonElement(True, MIDI_NOTE_TYPE, NAV_CHANNEL, TRACK_RIGHT_NOTE)
        self._left_button.add_value_listener(self._dsa_left)
        self._right_button.add_value_listener(self._dsa_right)
        self._refresh_button = ButtonElement(True, MIDI_NOTE_TYPE, NAV_CHANNEL, REFRESH_NOTE)
        self._refresh_button.add_value_listener(self._dsa_refresh)
        self._toggle_button = ButtonElement(True, MIDI_NOTE_TYPE, PAD_CHANNEL, PAD_TOGGLE_NOTE)
        self._toggle_button.add_value_listener(self._dsa_toggle)
        self._hold_button = ButtonElement(True, MIDI_NOTE_TYPE, PAD_CHANNEL, PAD_HOLD_NOTE)
        self._hold_button.add_value_listener(self._dsa_hold)

        # Xbox controller buttons: (button, callback) pairs, kept for disconnect()
        self._xbox_buttons = []
        for note, callback in (
            (PLAY_NOTE, self._dsa_play),
            (PAUSE_NOTE, self._dsa_pause),
            (STOP_NOTE, self._dsa_stop),
            (CLIP_LAUNCH_NOTE, self._dsa_clip_launch),
            (SCENE_UP_NOTE, lambda value: self._dsa_scene(-1, value)),
            (SCENE_DOWN_NOTE, lambda value: self._dsa_scene(1, value)),
            (STOP_TRACK_NOTE, self._dsa_stop_track),
            (STOP_ALL_NOTE, self._dsa_stop_all),
        ):
            button = ButtonElement(True, MIDI_NOTE_TYPE, XBOX_CHANNEL, note)
            button.add_value_listener(callback)
            self._xbox_buttons.append((button, callback))

    # ---------- navigation ----------

    def _session_visible(self):
        return self.application().view.is_view_visible("Session")

    # Vertical flicks: change track in the arrangement view, select clip in the session view.
    # Horizontal flicks only act in the session view (change track).
    def _dsa_prev(self, value):
        if not value:
            return
        if self._session_visible():
            self._select_clip(-1)
        else:
            self._move_track(-1)

    def _dsa_next(self, value):
        if not value:
            return
        if self._session_visible():
            self._select_clip(1)
        else:
            self._move_track(1)

    def _dsa_left(self, value):
        if value and self._session_visible():
            self._move_track(-1)

    def _dsa_right(self, value):
        if value and self._session_visible():
            self._move_track(1)

    def _dsa_volume(self, value):
        amount = (value - 64) / 63.0
        amount *= abs(amount)  # squared response: fine control near the center
        volume = self.song().view.selected_track.mixer_device.volume
        volume.value = max(volume.min, min(volume.max, volume.value + amount * VOLUME_FULL_STEP))

    def _dsa_toggle(self, value):
        if not value:
            return
        track = self.song().view.selected_track
        if TOGGLE_TARGET == "track":
            if not hasattr(track, "mute") or track == self.song().master_track:
                self.show_message("%s: cannot mute" % track.name)
                return
            track.mute = not track.mute
            self.show_message("%s: %s" % (track.name, "MUTED" if track.mute else "unmuted"))
            return

        rack = find_rack(track) if hasattr(track, "devices") else None
        if rack is None:
            self.show_message("%s: no rack to toggle" % track.name)
            return
        device_on = rack.parameters[0]
        device_on.value = 0.0 if device_on.value >= 0.5 else 1.0
        self.show_message("%s  |  %s: %s" % (
            track.name, rack.name, "ON" if device_on.value >= 0.5 else "OFF"))

    def _dsa_hold(self, value):
        if value:
            track = self.song().view.selected_track
            rack = find_rack(track) if hasattr(track, "devices") else None
            if rack is None:
                self.show_message("%s: no rack to switch on" % track.name)
                return
            self._held_device_on = rack.parameters[0]
            self._held_device_on.value = 1.0
            self.show_message("%s  |  %s: ON (held)" % (track.name, rack.name))
        elif self._held_device_on is not None:
            # Switch off the rack we switched on, even if the track selection moved meanwhile
            try:
                self._held_device_on.value = 0.0
            except RuntimeError:
                pass  # device was deleted while held
            self._held_device_on = None
            self.show_message("Instrument OFF")

    def _dsa_play(self, value):
        if value and not self.song().is_playing:
            self.song().continue_playing()
            self.show_message("Play")

    def _dsa_pause(self, value):
        # Live has no true pause: this stops playback and keeps the position
        if value and self.song().is_playing:
            self.song().stop_playing()
            self.show_message("Paused")

    def _dsa_stop(self, value):
        if value:
            song = self.song()
            song.stop_playing()
            song.current_song_time = 0.0
            self.show_message("Stopped")

    def _dsa_scene(self, direction, value):
        if not value:
            return
        song = self.song()
        scenes = list(song.scenes)
        try:
            index = scenes.index(song.view.selected_scene)
        except ValueError:
            index = 0
        index = max(0, min(len(scenes) - 1, index + direction))
        song.view.selected_scene = scenes[index]
        self.show_message("Scene %d: %s" % (index + 1, scenes[index].name))

    def _dsa_clip_launch(self, value):
        if not value:
            return
        slot = self.song().view.highlighted_clip_slot
        # Single clip only. An empty slot is never fired: with a stop button it would stop the track.
        if slot is None or not slot.has_clip:
            self.show_message("No clip selected")
            return
        slot.fire()
        self.show_message("Launched: %s" % slot.clip.name)

    def _dsa_stop_all(self, value):
        if value:
            self.song().stop_all_clips()
            self.show_message("All clips stopped")

    def _dsa_stop_track(self, value):
        if not value:
            return
        track = self.song().view.selected_track
        if hasattr(track, "stop_all_clips"):
            track.stop_all_clips()
            self.show_message("%s: clips stopped" % track.name)

    def _select_clip(self, direction):
        """Move the selection to the previous/next clip on the selected track (no launch)."""
        song = self.song()
        track = song.view.selected_track
        if not hasattr(track, "clip_slots"):
            self.show_message("%s has no clips" % track.name)
            return
        slots = list(track.clip_slots)
        scenes = list(song.scenes)
        try:
            index = scenes.index(song.view.selected_scene) + direction
        except ValueError:
            index = 0

        while 0 <= index < len(slots):
            if slots[index].has_clip:
                song.view.highlighted_clip_slot = slots[index]
                self.show_message("%s  |  scene %d: %s" % (track.name, index + 1, slots[index].clip.name))
                return
            index += direction
        self.show_message("%s: no more clips" % track.name)

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
        self._show_rack(track)
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
        self._show_rack(self.song().view.selected_track)

    def _show_rack(self, track):
        rack = find_rack(track) if hasattr(track, "devices") else None
        if rack is None:
            self.show_message("%s: no rack, knobs unassigned" % track.name)
        else:
            # Show the controlled rack in the Device view (selection only, no playback effect)
            self.song().view.select_device(rack)
            self.show_message("%s  |  %s" % (track.name, rack.name))

    # ---------- knob steps ----------

    def _target_track(self, target):
        song = self.song()
        if target == "selected":
            return song.view.selected_track
        if target == "pinned":
            try:
                return self._pinned if self._pinned in list(song.tracks) else None
            except RuntimeError:
                return None  # pinned track was deleted
        tracks = list(song.visible_tracks)
        try:
            index = tracks.index(song.view.selected_track) + target
        except ValueError:
            return None  # master/return selected
        return tracks[index] if index < len(tracks) else None

    def _target_param(self, track, p):
        if p == 0:
            return track.mixer_device.volume if hasattr(track, "mixer_device") else None
        rack = find_rack(track) if hasattr(track, "devices") else None
        if rack is None:
            return None
        params = rack.parameters  # parameters[0] is "Device On"; 1..16 are the macros
        return params[p] if p < len(params) else None

    def _dsa_step(self, target, p, value):
        track = self._target_track(target)
        if track is None:
            if target == "pinned":
                self.show_message("No track pinned: enter SPLIT to pin the selected track")
            return
        param = self._target_param(track, p)
        if param is None:
            return
        span = param.max - param.min
        param.value = max(param.min, min(param.max, param.value + (value - 64) * KNOB_STEP * span))
        self.show_message("%s  |  %s: %s" % (track.name, param.name, param.str_for_value(param.value)))

    def _dsa_pin(self, value):
        if value:
            self._pinned = self.song().view.selected_track
            self.show_message("Pinned: %s" % self._pinned.name)

    def _dsa_unpin(self, value):
        if value:
            self._pinned = None

    def _dsa_page(self, index, value):
        if not value:
            return
        name = PAGE_NAMES[index]
        pinned = self._target_track("pinned")
        if name in ("SPLIT", "LINK") and pinned is not None:
            self.show_message("Knobs: %s  |  pinned: %s" % (name, pinned.name))
        else:
            self.show_message("Knobs: %s" % name)

    def _dsa_mix_param(self, p, value):
        if value:
            self.show_message("Knobs: MIX  |  %s" % ("Volume" if p == 0 else "Macro %d" % p))

    def disconnect(self):
        view = self.song().view
        if view.selected_track_has_listener(self._dsa_selected_track_changed):
            view.remove_selected_track_listener(self._dsa_selected_track_changed)
        self._unwatch_devices()
        for element, callback in self._knob_listeners:
            element.remove_value_listener(callback)
        self._volume_encoder.remove_value_listener(self._dsa_volume)
        self._prev_button.remove_value_listener(self._dsa_prev)
        self._next_button.remove_value_listener(self._dsa_next)
        self._left_button.remove_value_listener(self._dsa_left)
        self._right_button.remove_value_listener(self._dsa_right)
        self._refresh_button.remove_value_listener(self._dsa_refresh)
        self._toggle_button.remove_value_listener(self._dsa_toggle)
        self._hold_button.remove_value_listener(self._dsa_hold)
        for button, callback in self._xbox_buttons:
            button.remove_value_listener(callback)
        super().disconnect()
