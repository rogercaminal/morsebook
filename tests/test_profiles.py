"""Tests for CW profile defaults and ordering.

The profile tests pin the default VHSC behavior and the API ordering used by
the browser profile selector.
"""

import app


def test_default_cw_params_match_vhsc_profile_at_volume_30():
    """Default CW parameters should match the VHSC built-in profile."""

    assert app.DEFAULT_PROFILE_NAME == "VHSC"
    assert app.CWParams().model_dump() == {
        "wpm": 40,
        "eff": 0,
        "freq": 600,
        "volume": 30,
        "ews": 0,
        "real": False,
    }


def test_missing_segment_params_fall_back_to_default_profile_values():
    """Missing per-segment settings should use default CW parameters."""

    assert app.row_params(None) == app.CWParams().model_dump()


def test_profiles_are_ordered_by_speed():
    """Profiles returned by the API should be ordered by speed."""

    profile_rows = app.profiles()

    speeds = [(row["params"]["wpm"], row["params"]["eff"], row["name"]) for row in profile_rows]
    assert speeds == sorted(speeds)


def test_vhsc_profile_is_default_and_uses_volume_30():
    """VHSC should remain the selected default profile at low volume."""

    profile_rows = app.profiles()
    vhsc = next(row for row in profile_rows if row["name"] == app.DEFAULT_PROFILE_NAME)

    assert app.DEFAULT_PROFILE_NAME == "VHSC"
    assert vhsc["params"]["volume"] == 30
    assert vhsc["params"] == app.CWParams().model_dump()
