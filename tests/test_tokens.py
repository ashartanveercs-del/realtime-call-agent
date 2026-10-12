from callagent.tokens import TokenStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_minted_token_can_be_claimed_exactly_once():
    store = TokenStore(ttl_seconds=60)
    token = store.mint({"role": "owner", "caller": "+15550001111"})

    assert store.claim(token) == {"role": "owner", "caller": "+15550001111"}
    assert store.claim(token) is None


def test_expired_token_is_rejected():
    clock = FakeClock()
    store = TokenStore(ttl_seconds=60, clock=clock)
    token = store.mint({"role": "guest"})

    clock.now += 61

    assert store.claim(token) is None


def test_unknown_or_empty_token_is_rejected():
    store = TokenStore()
    assert store.claim("not-a-real-token") is None
    assert store.claim("") is None


def test_tokens_are_long_and_unique():
    store = TokenStore()
    tokens = {store.mint({}) for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 32 for t in tokens)


def test_expired_tokens_are_purged_on_mint():
    clock = FakeClock()
    store = TokenStore(ttl_seconds=10, clock=clock)
    for _ in range(5):
        store.mint({})
    clock.now += 11
    store.mint({})
    assert len(store) == 1
