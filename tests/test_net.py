import types
from unittest import mock

from vizgui import net


def test_lan_cidr_reads_real_prefix():
    out = ("1: lo    inet 127.0.0.1/8 scope host lo\n"
           "2: enp8s0    inet 192.168.0.42/23 brd 192.168.1.255 scope global enp8s0\n")
    with mock.patch.object(net, "local_ip", return_value="192.168.0.42"), \
         mock.patch.object(net.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout=out, returncode=0)):
        assert net.lan_cidr() == "192.168.0.0/23"


def test_lan_cidr_fallback_to_24():
    with mock.patch.object(net, "local_ip", return_value="192.168.7.42"), \
         mock.patch.object(net.subprocess, "run", side_effect=OSError):
        assert net.lan_cidr() == "192.168.7.0/24"
