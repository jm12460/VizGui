from unittest import mock

from vizgui import net, platforms


def test_lan_cidr_delegates_to_platform_with_local_ip():
    with mock.patch.object(net, "local_ip", return_value="10.0.0.5"), \
         mock.patch.object(platforms, "lan_cidr", return_value="10.0.0.0/24") as m:
        assert net.lan_cidr() == "10.0.0.0/24"
    m.assert_called_once_with("10.0.0.5")
