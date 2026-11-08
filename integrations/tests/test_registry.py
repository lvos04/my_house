from shc_integrations.base import Adapter
from shc_integrations.registry import ADAPTERS, PROVIDERS, instructions_for


def test_registry_complete():
    assert set(ADAPTERS) == {"hue", "gree", "homewizard", "zigbee"}
    for name, cls in ADAPTERS.items():
        assert issubclass(cls, Adapter) and cls.provider == name
        assert instructions_for(name).steps
        inst = cls({}, {})
        assert inst.instructions().steps
    assert PROVIDERS["zigbee"].needs_manufacturer_app is None
