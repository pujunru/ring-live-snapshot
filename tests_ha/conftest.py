from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def allow_sockets(socket_enabled):
    """The fake camera runs a real aiortc peer over loopback UDP."""
    yield


@pytest.fixture(autouse=True)
def loopback_ice():
    """HA's test plugin fakes ifaddr adapters as 10.10.10.10; keep ICE on loopback."""
    with patch("aioice.ice.get_host_addresses", return_value=["127.0.0.1"]):
        yield
