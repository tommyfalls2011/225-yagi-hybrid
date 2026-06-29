"""Imperial-formatting helper tests."""
from hyagi.units import fmt_in, fmt_in_long, fmt_inches_only


def test_whole_inches_no_feet():
    assert fmt_in(0) == '0"'
    assert fmt_in(1) == '1"'
    assert fmt_in(11) == '11"'


def test_feet_rollup():
    assert fmt_in(12) == '1\' 0"'
    assert fmt_in(13) == '1\' 1"'
    assert fmt_in(215.5) == '17\' 11-1/2"'


def test_fraction_snapping_and_reduction():
    # 0.5" -> 1/2, 0.25" -> 1/4, 0.125" -> 1/8, etc. (denom=16 reduces).
    assert fmt_in(0.5) == '0-1/2"'
    assert fmt_in(0.25) == '0-1/4"'
    assert fmt_in(0.0625) == '0-1/16"'
    assert fmt_in(0.1875) == '0-3/16"'


def test_no_sixteen_over_sixteen():
    # 11.99 rounds to 12" (= 1' 0") at 1/16 resolution -- must not produce
    # "11 16/16".  Just below that boundary (11.96) stays as 11-15/16".
    assert fmt_in(11.96) == '11-15/16"'
    assert fmt_in(11.99) == '1\' 0"'


def test_negative():
    assert fmt_in(-1.5) == '-1-1/2"'
    assert fmt_in(-13.25) == '-1\' 1-1/4"'


def test_long_form():
    assert fmt_in_long(13.25) == '1 ft 1-1/4 in'
    assert fmt_in_long(0.5) == '0-1/2 in'


def test_inches_only():
    # No feet split -- total inches with optional 1/16ths fraction.
    assert fmt_inches_only(215.5) == '215-1/2"'
    assert fmt_inches_only(218.0) == '218"'
    assert fmt_inches_only(46.9) == '46-7/8"'


def test_with_decimal_suffix():
    out = fmt_in(215.72, with_decimal=True)
    assert '215.72 in' in out
    assert out.startswith("17' 11-")
