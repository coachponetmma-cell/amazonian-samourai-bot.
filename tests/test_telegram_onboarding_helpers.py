from app.services.telegram_bot import _athlete_name, _split_full_name


def test_split_full_name_handles_first_and_last_name():
    assert _split_full_name("Ponet Jason") == ("Ponet", "Jason")
    assert _split_full_name("Jason") == ("Jason", "")


def test_athlete_name_supports_both_database_schemas():
    assert _athlete_name({"full_name": "Jason Ponet"}, "Fallback") == "Jason Ponet"
    assert _athlete_name({"first_name": "Jason", "last_name": "Ponet"}, "Fallback") == "Jason Ponet"
    assert _athlete_name({}, "Fallback") == "Fallback"
