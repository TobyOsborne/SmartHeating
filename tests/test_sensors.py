"""Thermometer glitch filter."""

from custom_components.smart_heating.sensors import ReadingFilter


def test_zero_glitch_is_ignored_and_last_good_value_kept():
    f = ReadingFilter()
    assert f.feed(13.5, 0) == (13.5, 0)
    assert f.feed(0.0, 60) == (13.5, 0)  # the Bedroom's 0° glitch
    assert f.feed(0.0, 120) == (13.5, 0)
    assert f.feed(14.0, 180) == (14.0, 180)
    assert f.rejected == 2 and f.last_rejected == 0.0


def test_normal_changes_pass_straight_through():
    f = ReadingFilter()
    for i, t in enumerate([13.1, 13.6, 14.4, 16.1, 17.9, 19.5]):
        assert f.feed(t, i * 60) == (t, i * 60)


def test_big_jump_that_holds_steady_is_accepted_after_five_minutes():
    f = ReadingFilter()
    f.feed(14.0, 0)
    assert f.feed(21.0, 60) == (14.0, 0)
    assert f.feed(21.2, 200) == (14.0, 0)
    assert f.feed(21.1, 360) == (21.1, 360)  # sensor really was moved somewhere warmer


def test_out_of_range_and_startup_zero():
    f = ReadingFilter()
    assert f.feed(0.0, 0) is None  # nothing good yet
    assert f.feed(80.0, 60) is None
    assert f.feed(12.0, 120) == (12.0, 120)


def test_repeated_report_is_not_counted_twice():
    f = ReadingFilter()
    f.feed(14.0, 0)
    f.feed(0.0, 60)
    f.feed(0.0, 60)
    assert f.rejected == 1
