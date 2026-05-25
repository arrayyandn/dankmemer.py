from dankmemer import IN, Above, Below, Fuzzy, Range
from dankmemer.routes.base import matches_list, matches_numeric, matches_string


def test_matches_string_exact_fuzzy_and_membership():
    assert matches_string("Golden Bait", "golden bait")
    assert matches_string("Golden Bait", Fuzzy("golden", cutoff=70))
    assert matches_string("Golden Bait", IN("worm", "golden"))
    assert not matches_string("", "golden bait")
    assert not matches_string("Golden Bait", "worm")


def test_matches_numeric_exact_range_and_interfaces():
    assert matches_numeric(5, 5)
    assert matches_numeric("5", 5)
    assert matches_numeric(5, (1, 10))
    assert matches_numeric(5, Above(4))
    assert matches_numeric(5, Below(6))
    assert matches_numeric(5, Range(5, 10))
    assert not matches_numeric(None, 5)
    assert not matches_numeric("not-a-number", 5)


def test_matches_list_uses_string_matching_for_strings():
    assert matches_list(["Office", "Cafe"], "office")
    assert matches_list(["Office", "Cafe"], IN("caf"))
    assert matches_list([1, 2, 3], 2)
    assert not matches_list(["Office"], "Gym")
