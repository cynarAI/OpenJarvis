"""Tests for the network_devices tool."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from openjarvis.tools.network_devices import NetworkDevicesTool


def _mock_response(payload: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestNetworkDevicesToolSpec:
    def test_spec(self):
        tool = NetworkDevicesTool()
        assert tool.spec.name == "network_devices"
        assert tool.spec.category == "system"
        assert tool.spec.parameters["properties"]["scope"]["enum"] == [
            "all",
            "lan",
            "tailnet",
        ]
        assert tool.spec.parameters["required"] == []

    def test_tool_id(self):
        assert NetworkDevicesTool().tool_id == "network_devices"


class TestNetworkDevicesToolExecute:
    def test_service_unreachable(self):
        with patch(
            "openjarvis.tools.network_devices.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = NetworkDevicesTool().execute()
        assert result.success is False
        assert "unreachable" in result.content

    def test_defaults_to_scope_all(self):
        payload = {
            "tailnet": [
                {
                    "name": "evo",
                    "online": True,
                    "self": True,
                    "os": "linux",
                    "tailscale_ips": ["100.1.2.3"],
                }
            ],
            "lan": [
                {"ip": "192.168.0.5", "hostname": "printer", "mac": "aa:bb:cc:dd:ee:ff"}
            ],
            "lan_cidr": "192.168.0.0/24",
        }
        with patch(
            "openjarvis.tools.network_devices.urllib.request.urlopen",
            return_value=_mock_response(payload),
        ):
            result = NetworkDevicesTool().execute()
        assert result.success is True
        assert "Tailnet devices:" in result.content
        assert "evo (this machine): online, linux, 100.1.2.3" in result.content
        assert "Local network devices (192.168.0.0/24):" in result.content
        assert "192.168.0.5: printer (aa:bb:cc:dd:ee:ff)" in result.content

    def test_scope_lan_omits_tailnet_section(self):
        payload = {
            "tailnet": [],
            "lan": [{"ip": "10.0.0.1"}],
            "lan_cidr": "10.0.0.0/24",
        }
        with patch(
            "openjarvis.tools.network_devices.urllib.request.urlopen",
            return_value=_mock_response(payload),
        ):
            result = NetworkDevicesTool().execute(scope="lan")
        assert result.success is True
        assert "Tailnet devices:" not in result.content
        assert "Local network devices" in result.content

    def test_scope_tailnet_omits_lan_section(self):
        payload = {
            "tailnet": [{"name": "air", "online": False}],
            "lan": [],
            "lan_cidr": "?",
        }
        with patch(
            "openjarvis.tools.network_devices.urllib.request.urlopen",
            return_value=_mock_response(payload),
        ):
            result = NetworkDevicesTool().execute(scope="tailnet")
        assert result.success is True
        assert "Tailnet devices:" in result.content
        assert "air: offline" in result.content
        assert "Local network devices" not in result.content

    def test_invalid_scope_falls_back_to_all(self):
        payload = {"tailnet": [], "lan": [], "lan_cidr": "?"}
        with patch(
            "openjarvis.tools.network_devices.urllib.request.urlopen",
            return_value=_mock_response(payload),
        ):
            result = NetworkDevicesTool().execute(scope="bogus")
        assert result.success is True
        assert "Tailnet devices:" in result.content
        assert "Local network devices" in result.content

    def test_tailnet_lookup_error_surfaced_per_entry(self):
        payload = {
            "tailnet": [{"error": "tailscale not running"}],
            "lan": [],
            "lan_cidr": "?",
        }
        with patch(
            "openjarvis.tools.network_devices.urllib.request.urlopen",
            return_value=_mock_response(payload),
        ):
            result = NetworkDevicesTool().execute(scope="tailnet")
        assert result.success is True
        assert "tailscale not running" in result.content

    def test_empty_device_list_still_prints_section_header(self):
        payload = {"tailnet": [], "lan": [], "lan_cidr": "10.0.0.0/24"}
        with patch(
            "openjarvis.tools.network_devices.urllib.request.urlopen",
            return_value=_mock_response(payload),
        ):
            result = NetworkDevicesTool().execute(scope="lan")
        assert result.success is True
        assert "Local network devices (10.0.0.0/24):" in result.content
