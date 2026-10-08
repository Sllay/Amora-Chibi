from amora.identity import build_initial_registry


def test_bk_aliases_resolve_to_same_entity() -> None:
    registry = build_initial_registry()
    ids = {registry.resolve_alias(alias).id for alias in ("BK", "Bad King", "Slay", "Nick") if registry.resolve_alias(alias) is not None}
    assert ids == {"PERSON_BK"}


def test_relational_words_are_not_global_aliases() -> None:
    registry = build_initial_registry()
    assert registry.resolve_alias("pai") is None
    assert registry.resolve_alias("mãe") is None
    assert registry.resolve_alias("filha") is None
