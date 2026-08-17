import math

from wificsi.web.transform import amplitude_stats, iq_to_amplitude


def test_known_iq_amplitude_vector():
    # (real=3, imag=4) -> 5.0 and (real=0, imag=5) -> 5.0
    assert iq_to_amplitude(bytes([3, 4, 0, 5]), False) == (5.0, 5.0)


def test_first_word_invalid_skips_leading_four_bytes():
    iq = bytes([9, 9, 9, 9, 3, 4])
    assert iq_to_amplitude(iq, True) == (5.0,)


def test_negative_int8_values():
    # (real=-3, imag=4) -> 5.0
    assert iq_to_amplitude(bytes([0xFD, 4]), False) == (5.0,)


def test_odd_length_drops_trailing_byte():
    assert iq_to_amplitude(bytes([3, 4, 5]), False) == (5.0,)


def test_empty_iq_is_empty_amplitude():
    assert iq_to_amplitude(b"", False) == ()


def test_amplitude_stats_mean_rms_variance():
    mean, rms, variance = amplitude_stats((3.0, 4.0))
    assert mean == 3.5
    assert rms == math.sqrt(12.5)
    assert variance == 0.25


def test_amplitude_stats_empty():
    assert amplitude_stats(()) == (0.0, 0.0, 0.0)
