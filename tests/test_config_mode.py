import pytest

from portorium.config import Settings


def make(**kw):
    base = dict(nvidia_api_key="", nightshift_url="", nightshift_token="", zetaris_url="", zetaris_username="",
                zetaris_password="", zetaris_org_id="1", sample_mode=False)
    return Settings(**{**base, **kw})


def test_no_credentials_at_all_is_sample_mode():
    assert make().live is False
    make().check()


def test_every_credential_is_live_mode():
    s = make(nvidia_api_key="k", nightshift_token="t", zetaris_username="u", zetaris_password="p")
    assert s.live is True
    s.check()


def test_some_credentials_without_sample_mode_fail_instead_of_replaying():
    with pytest.raises(RuntimeError, match="NIGHTSHIFT"):
        make(nvidia_api_key="k", zetaris_username="u", zetaris_password="p").check()


def test_sample_mode_requested_ignores_credentials():
    make(nvidia_api_key="k", sample_mode=True).check()
