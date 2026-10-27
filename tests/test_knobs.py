from knobs import (
    MIX_BASE, MIX_PARAM_NOTE_BASE, MIX_STRIDE, PAGE_NOTE_BASE, PINNED_BASE, PIN_NOTE,
    SELECTED_BASE, UNPIN_NOTE, KnobDecoder, KnobPages, decode_relative,
)


def test_absolute_first_value_is_reference_only():
    decoder = KnobDecoder("absolute")
    assert decoder.step(0, 100) == 0
    assert decoder.step(0, 103) == 3
    assert decoder.step(0, 101) == -2


def test_absolute_knobs_are_independent():
    decoder = KnobDecoder("absolute")
    decoder.step(0, 10)
    decoder.step(1, 90)
    assert decoder.step(0, 12) == 2
    assert decoder.step(1, 85) == -5


def test_absolute_jump_is_clamped():
    decoder = KnobDecoder("absolute")
    decoder.step(0, 0)
    assert decoder.step(0, 127) == 16


def test_relative_modes():
    assert decode_relative(65, "relative_offset") == 1
    assert decode_relative(62, "relative_offset") == -2
    assert decode_relative(3, "relative_twos") == 3
    assert decode_relative(126, "relative_twos") == -2
    assert decode_relative(2, "relative_signed") == 2
    assert decode_relative(66, "relative_signed") == -2


def test_device_page_targets_selected_macros():
    pages = KnobPages()
    assert pages.knob(0, 2) == [("cc", SELECTED_BASE + 1, 66)]
    assert pages.knob(7, -3) == [("cc", SELECTED_BASE + 8, 61)]


def test_zero_step_sends_nothing():
    assert KnobPages().knob(0, 0) == []


def test_entering_split_pins_and_device_unpins():
    pages = KnobPages()
    assert pages.press() == [("note", PIN_NOTE), ("note", PAGE_NOTE_BASE + 1)]
    assert pages.page == "split"
    pages.press()  # link: keeps the pin
    pages.press()  # mix
    assert pages.press() == [("note", UNPIN_NOTE), ("note", PAGE_NOTE_BASE + 0)]
    assert pages.page == "device"


def test_split_page_halves():
    pages = KnobPages()
    pages.press()
    assert pages.knob(0, -1) == [("cc", SELECTED_BASE + 1, 63)]
    assert pages.knob(3, -1) == [("cc", SELECTED_BASE + 4, 63)]
    assert pages.knob(4, -1) == [("cc", PINNED_BASE + 1, 63)]
    assert pages.knob(7, -1) == [("cc", PINNED_BASE + 4, 63)]


def test_link_page_moves_both_tracks():
    pages = KnobPages()
    pages.press()
    pages.press()
    assert pages.page == "link"
    assert pages.knob(2, 1) == [("cc", SELECTED_BASE + 3, 65), ("cc", PINNED_BASE + 3, 65)]


def test_mix_page_one_knob_per_track():
    pages = KnobPages()
    for _ in range(3):
        pages.press()
    assert pages.page == "mix"
    assert pages.knob(0, 1) == [("cc", MIX_BASE + 0, 65)]  # volume of selected track
    assert pages.knob(5, 1) == [("cc", MIX_BASE + 5 * MIX_STRIDE, 65)]


def test_mix_param_follows_encoder_and_stays_in_range():
    pages = KnobPages()
    for _ in range(3):
        pages.press()
    assert pages.turn(-20) == []  # already at volume, can't go lower
    assert pages.turn(4) == []  # not enough yet
    assert pages.turn(4) == [("note", MIX_PARAM_NOTE_BASE + 1)]
    assert pages.knob(2, 1) == [("cc", MIX_BASE + 2 * MIX_STRIDE + 1, 65)]


def test_encoder_turn_ignored_outside_mix():
    assert KnobPages().turn(50) == []


def test_large_step_is_clamped_to_midi_range():
    assert KnobPages().knob(0, 100) == [("cc", SELECTED_BASE + 1, 127)]


def test_live_script_uses_the_same_wire_numbers():
    # The Live script runs in Live's own Python and can't import knobs.py, so it copies the numbers
    import re
    from pathlib import Path

    import knobs

    src = (Path(__file__).parent.parent / "remote_script/DualSenseArturia/DualSenseArturia.py").read_text(encoding="utf-8")
    for name in ("MACRO_CHANNEL", "SELECTED_BASE", "PINNED_BASE", "MIX_BASE", "MIX_STRIDE",
                 "PIN_NOTE", "UNPIN_NOTE", "PAGE_NOTE_BASE", "MIX_PARAM_NOTE_BASE"):
        assert int(re.search(rf"^{name} = (\d+)", src, re.M).group(1)) == getattr(knobs, name), name
    names = re.search(r"^PAGE_NAMES = \((.*)\)", src, re.M).group(1)
    assert [n.strip(' "').lower() for n in names.split(",")] == list(knobs.PAGES)
