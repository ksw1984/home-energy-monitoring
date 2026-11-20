from dataclasses import replace

from src.bems.battery_adapter.factory import create_adapter, with_mock_identities
from src.bems.services.live import MockBatteryAdapter
from src.config.config import config_obj


def _bems():
    bems = config_obj.bems
    assert bems is not None
    return bems


def _mock_device():
    return replace(
        _bems().devices[0],
        id="mock_live",
        mode="live",
        attributes={"mock": True},
    )


def test_create_adapter_uses_mock_for_explicit_mock_attribute():
    adapter, client = create_adapter(_mock_device(), _bems(), dry_run=False)

    assert isinstance(adapter, MockBatteryAdapter)
    assert client is None


def test_with_mock_identities_changes_only_selected_devices():
    mock_device = _mock_device()
    real_device = replace(
        mock_device,
        id="real_live",
        attributes={
            "vendor": "anker",
            "expected_model": "A17E2",
            "supported_firmware_versions": ["1.0.0"],
        },
    )
    config = replace(_bems(), devices=[mock_device, real_device])

    updated = with_mock_identities(config, device_ids={mock_device.id})

    assert updated.devices[0].attributes["mock"] is True
    assert updated.devices[0].attributes["expected_model"] == "BEMS-MOCK"
    assert updated.devices[0].attributes["supported_firmware_versions"] == ["mock"]
    assert updated.devices[1].attributes["expected_model"] == "A17E2"
    assert updated.devices[1].attributes["supported_firmware_versions"] == ["1.0.0"]
