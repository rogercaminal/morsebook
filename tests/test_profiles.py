import app


def test_default_cw_params_match_vhsc_profile_at_volume_30():
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
    assert app.row_params(None) == app.CWParams().model_dump()


def test_vhsc_profile_is_first_and_uses_volume_30():
    profile_rows = app.profiles()

    assert profile_rows[0]["name"] == "VHSC"
    assert profile_rows[0]["params"]["volume"] == 30
    assert profile_rows[0]["params"] == app.CWParams().model_dump()
