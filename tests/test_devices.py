import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import main


class DeviceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rms-device-tests-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "devices.json"
        self.path_patch = patch.object(main, "DEVICES_PATH", self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.base = [
            {"mac": "aabbccddeeff", "name": "First", "profile": "nameplate7", "status": "base"},
            {"mac": "112233445566", "name": "Already sent", "profile": "nameplate10", "status": "filled"},
        ]
        self.write(self.base)

    def write(self, devices):
        self.path.write_text(json.dumps({"devices": devices}), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["devices"]

    def gateway(self, devices):
        return patch.object(main, "gateway_post", return_value={"message": {"data": devices}})

    def save(self, edited, base=None):
        return main.devices_file_set(main.DevicesFile(
            devices=edited, base_devices=self.base if base is None else base,
        ))

    def test_discovery_adds_defaults_and_keeps_existing_and_disconnected_devices(self):
        with self.gateway([
            {"mac": "11:22:33:44:55:66", "name": "Gateway name", "Status": "online"},
            {"mac": "77:88:99:AA:BB:CC", "Status": "online"},
            {"mac": "7788.99aa.bbcc", "Status": "online"},
            {"mac": "invalid"}, {"mac": 123}, None,
        ]):
            result = main.discover_devices()
        self.assertEqual(result["added"], 1)
        self.assertEqual(self.read(), self.base + [
            {"mac": "778899aabbcc", "name": "", "profile": "nameplate7", "status": "base"},
        ])

    def test_repeated_scan_does_not_rewrite_file(self):
        before = self.path.read_bytes()
        modified = self.path.stat().st_mtime_ns
        with self.gateway([{"mac": "AA:BB:CC:DD:EE:FF", "Status": "online"}]):
            self.assertEqual(main.discover_devices()["added"], 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, modified)

    def test_status_change_during_gateway_request_is_preserved(self):
        requested = threading.Event()
        release = threading.Event()
        failures = []

        def gateway(*args, **kwargs):
            requested.set()
            if not release.wait(2):
                raise TimeoutError("Test did not release gateway response")
            return {"message": {"data": [{"mac": "778899aabbcc", "Status": "online"}]}}

        def scan():
            try:
                main.discover_devices()
            except Exception as exc:
                failures.append(exc)

        with patch.object(main, "gateway_post", side_effect=gateway):
            worker = threading.Thread(target=scan)
            worker.start()
            try:
                self.assertTrue(requested.wait(2))
                main.mark_filled(main.MacRequest(mac=self.base[0]["mac"]))
            finally:
                release.set()
                worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.read()[0]["status"], "filled")
        self.assertEqual(self.read()[-1]["mac"], "778899aabbcc")

    def test_gateway_errors_and_invalid_responses_do_not_modify_file(self):
        before = self.path.read_bytes()
        for response in ({}, {"message": {}}, {"message": {"data": None}}, {"message": {"data": {}}}):
            with self.subTest(response=response), patch.object(main, "gateway_post", return_value=response):
                with self.assertRaises(HTTPException):
                    main.discover_devices()
                self.assertEqual(self.path.read_bytes(), before)
        with patch.object(main, "gateway_post", side_effect=HTTPException(502, "Offline")):
            with self.assertRaises(HTTPException):
                main.discover_devices()
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_devices_file_is_not_replaced_with_discovered_devices(self):
        self.path.write_text('{"devices": [', encoding="utf-8")
        before = self.path.read_bytes()
        with self.gateway([{"mac": "778899aabbcc", "Status": "online"}]):
            with self.assertRaises(HTTPException):
                main.discover_devices()
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_registry_can_be_created(self):
        self.path.unlink()
        with self.gateway([{"mac": "778899aabbcc", "Status": "online"}]):
            main.discover_devices()
        self.assertEqual(self.read()[0], {"mac": "778899aabbcc", "name": "", "profile": "nameplate7", "status": "base"})

    def test_failed_atomic_replace_preserves_file_and_cleans_temporary_file(self):
        before = self.path.read_bytes()
        with self.gateway([{"mac": "778899aabbcc", "Status": "online"}]), patch.object(main.os, "replace", side_effect=OSError("Disk error")):
            with self.assertRaises(OSError):
                main.discover_devices()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".devices-*.tmp")), [])

    def test_stale_editor_preserves_discovery_and_new_filled_status(self):
        edited = deepcopy(self.base)
        edited[0]["name"] = "Operator name"
        with self.gateway([{"mac": "778899aabbcc", "Status": "online"}]):
            main.discover_devices()
        main.mark_filled(main.MacRequest(mac=self.base[0]["mac"]))
        result = self.save(edited)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["devices"][0]["name"], "Operator name")
        self.assertEqual(result["devices"][0]["status"], "filled")
        self.assertEqual(result["devices"][1], self.base[1])
        self.assertEqual(result["devices"][2]["mac"], "778899aabbcc")
        self.assertEqual(self.read(), result["devices"])

    def test_conflicting_name_edit_is_rejected_without_writing(self):
        first = deepcopy(self.base)
        first[0]["name"] = "First operator"
        self.save(first)
        before = self.path.read_bytes()
        second = deepcopy(self.base)
        second[0]["name"] = "Second operator"
        with self.assertRaises(HTTPException) as error:
            self.save(second)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.path.read_bytes(), before)

    def test_editor_deletion_preserves_concurrently_discovered_device(self):
        with self.gateway([{"mac": "778899aabbcc", "Status": "online"}]):
            main.discover_devices()
        result = self.save([self.base[1]])
        self.assertEqual([d["mac"] for d in result["devices"]], [self.base[1]["mac"], "778899aabbcc"])

    def test_stale_deletion_cannot_remove_recently_filled_device(self):
        main.mark_filled(main.MacRequest(mac=self.base[0]["mac"]))
        before = self.path.read_bytes()
        with self.assertRaises(HTTPException) as error:
            self.save([self.base[1]])
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.path.read_bytes(), before)

    def test_manual_entry_and_discovery_of_same_mac_merge_without_duplicates(self):
        new = {"mac": "778899aabbcc", "name": "Manual name", "profile": "nameplate7", "status": "base"}
        with self.gateway([{"mac": new["mac"], "Status": "online"}]):
            main.discover_devices()
        main.mark_filled(main.MacRequest(mac=new["mac"]))
        result = self.save(self.base + [new])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["devices"][-1]["name"], "Manual name")
        self.assertEqual(result["devices"][-1]["status"], "filled")

    def test_invalid_or_duplicate_mac_and_missing_snapshot_rejected(self):
        for edited in (
            [dict(self.base[0], mac="bad")],
            self.base + [dict(self.base[0], mac="AA:BB:CC:DD:EE:FF")],
        ):
            with self.subTest(edited=edited), self.assertRaises(ValidationError):
                main.DevicesFile(devices=edited, base_devices=self.base)
        with self.assertRaises(ValidationError):
            main.DevicesFile(devices=self.base)

    def test_filled_stays_hidden_until_explicit_reset(self):
        gateway = [dict(d, IPAdd="127.0.0.1", Status="online") for d in self.base]
        with self.gateway(gateway), patch.object(main, "ping_probe", return_value=True):
            main.discover_devices()
            self.assertEqual([d["mac"] for d in main.devices_list()["devices"]], [self.base[0]["mac"]])
            main.mark_filled(main.MacRequest(mac=self.base[0]["mac"]))
            main.discover_devices()
            self.assertEqual(main.devices_list()["devices"], [])
            main.refresh_devices()
            self.assertEqual(len(main.devices_list()["devices"]), 2)
        self.assertTrue(all(d["status"] == "base" for d in self.read()))

    def test_offline_record_with_reused_ip_is_not_discovered_or_shown(self):
        current = {"mac": "d48afcefab38", "name": "Wireless", "profile": "nameplate7", "status": "base"}
        stale = {"mac": "38182b8241e4", "name": "Old device", "profile": "nameplate7", "status": "base"}
        gateway = [
            {"mac": stale["mac"], "IPAdd": "10.30.30.252", "Status": "offline"},
            {"mac": current["mac"], "IPAdd": "10.30.30.252", "Status": "online"},
        ]
        for rows in (gateway, list(reversed(gateway))):
            with self.subTest(rows=rows), self.gateway(rows), patch.object(main, "ping_probe", return_value=True) as ping:
                self.write([])
                self.assertEqual(main.discover_devices()["macs"], [current["mac"]])
                self.assertEqual([d["mac"] for d in self.read()], [current["mac"]])
                # Also hide stale entries imported by previous versions, preserving operator data.
                self.write([current, stale])
                self.assertEqual([d["mac"] for d in main.devices_list()["devices"]], [current["mac"]])
                ping.assert_called_once_with("10.30.30.252")
                main.mark_filled(main.MacRequest(mac=current["mac"]))
                self.assertEqual(main.discover_devices()["added"], 0)
                self.assertEqual(main.devices_list()["devices"], [])
                self.assertEqual(self.read(), [dict(current, status="filled"), stale])

    def test_unconfirmed_gateway_status_is_not_discovered_or_shown(self):
        for fields in ({}, {"Status": None}, {"Status": ""}, {"Status": 1}, {"Status": "unknown"}, {"Status": "offline"}):
            with self.subTest(fields=fields):
                row = dict(mac="778899aabbcc", IPAdd="127.0.0.1", **fields)
                with self.gateway([row]):
                    before = self.path.read_bytes()
                    self.assertEqual(main.discover_devices()["added"], 0)
                    self.assertEqual(self.path.read_bytes(), before)
                row["mac"] = self.base[0]["mac"]
                with self.gateway([row]), patch.object(main, "ping_probe", return_value=True) as ping:
                    self.assertEqual(main.devices_list()["devices"], [])
                    ping.assert_not_called()

    def test_device_is_added_when_it_comes_online_and_retained_when_offline(self):
        row = {"mac": "778899aabbcc", "IPAdd": "127.0.0.1", "Status": "offline"}
        with self.gateway([row]):
            self.assertEqual(main.discover_devices()["added"], 0)
            row["Status"] = " Online "
            self.assertEqual(main.discover_devices()["macs"], [row["mac"]])
            self.assertEqual(self.read()[-1], {"mac": row["mac"], "name": "", "profile": "nameplate7", "status": "base"})
            with patch.object(main, "ping_probe", return_value=True):
                self.assertEqual(main.devices_list()["devices"][0]["status_gateway"], "online")
            row["Status"] = "offline"
            before = self.path.read_bytes()
            self.assertEqual(main.discover_devices()["added"], 0)
            self.assertEqual(self.path.read_bytes(), before)
            with patch.object(main, "ping_probe", return_value=True) as ping:
                self.assertEqual(main.devices_list()["devices"], [])
                ping.assert_not_called()

    def test_lifespan_polls_without_browser_recovers_from_failure_and_stops(self):
        recovered = threading.Event()
        calls = []

        def gateway(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise HTTPException(502, "Offline")
            recovered.set()
            return {"message": {"data": [{"mac": "778899aabbcc", "Status": "online"}]}}

        async def run():
            async with main.lifespan(main.app):
                self.assertTrue(await asyncio.to_thread(recovered.wait, 2))

        with patch.object(main, "gateway_post", side_effect=gateway), patch.object(main, "DISCOVERY_INTERVAL_SECONDS", 0.02):
            with self.assertLogs(main.logger, level="WARNING"):
                asyncio.run(run())
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("778899aabbcc", [d["mac"] for d in self.read()])
        self.assertFalse(any(t.name == "tablecard-discovery" and t.is_alive() for t in threading.enumerate()))

    def test_name_editor_reads_saved_devices_without_gateway(self):
        with patch.object(main, "gateway_post") as gateway:
            result = main.devices_names_get()
        self.assertEqual(result["devices"], [{"mac": d["mac"], "name": d["name"]} for d in self.base])
        gateway.assert_not_called()

    def test_name_save_preserves_concurrent_status_profile_and_discovery(self):
        current = deepcopy(self.base)
        current[0]["status"] = "filled"
        current[0]["profile"] = "nameplate10"
        current.append({"mac": "778899aabbcc", "name": "", "profile": "nameplate7", "status": "base"})
        self.write(current)
        result = main.devices_names_set(main.DeviceNamesUpdate(changes=[{
            "mac": "AA:BB:CC:DD:EE:FF", "name": "  Operator name  ", "base_name": "First",
        }]))
        current[0]["name"] = "Operator name"
        self.assertEqual(self.read(), current)
        self.assertEqual(result["devices"][0]["name"], "Operator name")
        self.assertEqual(set(result["devices"][0]), {"mac", "name"})

    def test_name_conflict_and_deleted_device_reject_entire_batch(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                current = deepcopy(self.base)
                current[1]["name"] = "Another operator"
                self.write(current[:1] if missing else current)
                before = self.path.read_bytes()
                with self.assertRaises(HTTPException) as error:
                    main.devices_names_set(main.DeviceNamesUpdate(changes=[
                        {"mac": d["mac"], "base_name": d["name"], "name": "New name"} for d in self.base
                    ]))
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(self.path.read_bytes(), before)

    def test_name_endpoint_rejects_status_edits_and_duplicate_macs(self):
        edit = {"mac": self.base[0]["mac"], "base_name": "First", "name": "New"}
        for changes in ([dict(edit, status="base")], [edit, edit], [dict(edit, name="x" * 121)]):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                main.DeviceNamesUpdate(changes=changes)

    def test_list_explains_gateway_failure_without_changing_devices(self):
        before = self.path.read_bytes()
        with patch.object(main, "gateway_post", side_effect=HTTPException(502, "Timeout")):
            result = main.devices_list()
        self.assertFalse(result["gateway_reachable"])
        self.assertEqual(result["counts"]["registered"], 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_list_counts_distinguish_filled_offline_and_unreachable(self):
        with self.gateway([{**self.base[0], "Status": "online", "IPAdd": "127.0.0.1"}]), patch.object(main, "ping_probe", return_value=False):
            result = main.devices_list()
            self.assertEqual(result["counts"], {"registered": 2, "filled": 1, "offline": 0, "unreachable": 1})
        with self.gateway([]):
            self.assertEqual(main.devices_list()["counts"], {"registered": 2, "filled": 1, "offline": 1, "unreachable": 0})


if __name__ == "__main__":
    unittest.main()
