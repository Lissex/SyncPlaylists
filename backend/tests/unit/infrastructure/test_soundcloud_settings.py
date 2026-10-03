"""Официальный API SoundCloud включается только заданным приложением: пустые
PLATFORMS__SOUNDCLOUD__OFFICIAL__* из docker-compose его не включают."""

from syncplaylists.infrastructure.config.settings import SoundCloudSettings


def test_official_disabled_by_default() -> None:
    assert SoundCloudSettings().official is None


def test_blank_official_from_compose_is_disabled() -> None:
    settings = SoundCloudSettings.model_validate(
        {"official": {"client_id": "", "client_secret": ""}, "client_id_override": ""}
    )
    assert settings.official is None
    assert settings.client_id_override is None


def test_official_enabled_with_app() -> None:
    settings = SoundCloudSettings.model_validate(
        {"official": {"client_id": "app", "client_secret": "s3cr3t-value"}}
    )
    assert settings.official is not None
    assert settings.official.client_id == "app"
    assert settings.official.client_secret.get_secret_value() == "s3cr3t-value"
    assert "s3cr3t-value" not in repr(settings)
