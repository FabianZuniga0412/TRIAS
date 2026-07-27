from authorization import AuthorizationStore


def test_authorizations_combine_env_and_persisted_changes(tmp_path):
    path = tmp_path / "authorized_users.json"
    store = AuthorizationStore(path=path, base_users=frozenset({10}), admins=frozenset({99}))
    store.load()

    assert store.is_authorized(10)
    assert store.is_authorized(99)
    assert store.allow(20)
    assert store.revoke(10)
    assert not store.is_authorized(10)
    assert store.is_authorized(20)

    restored = AuthorizationStore(path=path, base_users=frozenset({10}), admins=frozenset({99}))
    restored.load()
    assert not restored.is_authorized(10)
    assert restored.is_authorized(20)
    assert restored.is_authorized(99)


def test_administrator_cannot_be_revoked(tmp_path):
    store = AuthorizationStore(path=tmp_path / "users.json", admins=frozenset({99}))
    store.load()

    try:
        store.revoke(99)
    except ValueError as exc:
        assert "administrador" in str(exc)
    else:
        raise AssertionError("Se esperaba que la revocacion de administrador fallara")
