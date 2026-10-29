import pytest
from pydantic import ValidationError

from tests.conftest import make_settings

GOOD_PROD = {
    "env": "production",
    "auth_mode": "github",
    "base_url": "https://pi.example.ts.net",
    "github_client_id": "id",
    "github_client_secret": "secret",
    "jwt_signing_key": "k" * 40,
    "allowed_github_logins": "Emenowicz, other",
}


make = make_settings


def test_defaults_are_local_development():
    s = make()
    assert (s.env, s.auth_mode, s.host, s.is_loopback) == ("development", "none", "127.0.0.1", True)


def test_production_config_parses_logins():
    assert make(**GOOD_PROD).allowed_github_logins == ["Emenowicz", "other"]


@pytest.mark.parametrize(
    "override",
    [
        {"auth_mode": "none"},
        {"base_url": "http://pi.example.ts.net"},
        {"jwt_signing_key": "short"},
        {"allowed_github_logins": ""},
        {"github_client_secret": ""},
    ],
)
def test_production_fails_closed(override):
    with pytest.raises(ValidationError):
        make(**{**GOOD_PROD, **override})


def test_env_variables(monkeypatch):
    monkeypatch.setenv("PLMCP_ALLOWED_HOSTS", "a.example, b.example")
    assert make().allowed_hosts == ["a.example", "b.example"]
