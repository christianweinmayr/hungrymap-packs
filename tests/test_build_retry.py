import urllib.error

import pytest

from packs.build import with_retries


def test_retries_transient_errors_then_succeeds():
    calls, slept = [], []
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError("u", 502, "Bad Gateway", None, None)
        return "ok"
    assert with_retries(flaky, "x", delays=(1, 2, 3), sleep=slept.append) == "ok"
    assert slept == [1, 2]


def test_gives_up_after_last_retry_and_on_permanent_errors():
    with pytest.raises(TimeoutError):
        with_retries(lambda: (_ for _ in ()).throw(TimeoutError("read timed out")), "x", delays=(1,), sleep=lambda _: None)
    calls = []
    def missing():
        calls.append(1)
        raise urllib.error.HTTPError("u", 404, "Not Found", None, None)
    with pytest.raises(urllib.error.HTTPError):
        with_retries(missing, "x", delays=(1, 2), sleep=lambda _: None)
    assert len(calls) == 1
