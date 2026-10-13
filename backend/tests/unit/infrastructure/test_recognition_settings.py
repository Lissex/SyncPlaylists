import pytest
from pydantic import ValidationError

from syncplaylists.infrastructure.config.settings import RecognitionSettings


def test_defaults_shazam_only() -> None:
    settings = RecognitionSettings()

    assert settings.fragment_seconds == 18
    assert settings.shazam.enabled
    assert settings.acrcloud is None
    assert settings.transfer_phase_limit_minutes == 15


@pytest.mark.parametrize("seconds", [14, 21])
def test_fragment_must_be_15_to_20(seconds: int) -> None:
    with pytest.raises(ValidationError, match="от 15 до 20"):
        RecognitionSettings(fragment_seconds=seconds)


def test_blank_acrcloud_from_compose_is_disabled() -> None:
    settings = RecognitionSettings.model_validate(
        {"acrcloud": {"host": "", "access_key": "", "access_secret": ""}}
    )

    assert settings.acrcloud is None


def test_acrcloud_enabled_when_configured() -> None:
    settings = RecognitionSettings.model_validate(
        {
            "acrcloud": {
                "host": "identify-eu-west-1.acrcloud.com",
                "access_key": "k",
                "access_secret": "very-secret-value",
            }
        }
    )

    assert settings.acrcloud is not None
    assert settings.acrcloud.access_secret.get_secret_value() == "very-secret-value"
    assert "very-secret-value" not in repr(settings.acrcloud.access_secret)
