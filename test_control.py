import copy
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import control as c


class FakeHardware(c.Hardware):
    """Model the IT8613 shared PWM/auto-start register using temporary files."""
    def __init__(self, directory):
        directory = Path(directory)
        super().__init__(directory / "hwmon", directory / "dmi")
        self.root.mkdir(exist_ok=True)
        self.dmi.mkdir(exist_ok=True)
        (self.dmi / "board_vendor").write_text("AZW")
        (self.dmi / "board_name").write_text("EQ")
        chip = self.root / "hwmon0"
        if not chip.exists():
            chip.mkdir()
            (chip / "name").write_text("it8613")
            for key, start in [("cpu", 95), ("system", 80)]:
                n = c.FANS[key][0]
                for attr, value in [("_enable", 2), ("_auto_start", start), ("", start)]:
                    (chip / ("pwm%d%s" % (n, attr))).write_text(str(value))
                (chip / ("fan%d_input" % n)).write_text("3000")

    def write(self, path, value):
        super().write(path, value)
        path = Path(path)
        if path.name in ["pwm2", "pwm3"]:
            super().write(path.with_name(path.name + "_auto_start"), value)
        elif path.name.endswith("_auto_start"):
            super().write(path.with_name(path.name.replace("_auto_start", "")), value)
        elif path.name.endswith("_enable") and value == 0:
            pwm = path.with_name(path.name.replace("_enable", ""))
            super().write(pwm, 255)
            super().write(pwm.with_name(pwm.name + "_auto_start"), 255)


def configuration():
    return {"version": 1, "emergency_c": 85, "hysteresis_c": 2, "fans": {
        key: {"enabled": True, "sensor": "a" * 24,
              "points": [{"temp": 40, "percent": 40}, {"temp": 80, "percent": 100}]}
        for key in c.FANS}}


class CurveTests(unittest.TestCase):
    def test_interpolation_and_endpoints(self):
        points = configuration()["fans"]["cpu"]["points"]
        for temp, expected in [(20, 40), (40, 40), (60, 70), (80, 100), (100, 100)]:
            self.assertEqual(c.curve_percent(points, temp), expected)

    def test_sensor_failure_cpu_guard_and_stall_override_other_sensor(self):
        fan = configuration()["fans"]["system"]
        self.assertEqual(c.target_percent(fan, None, 65, 3000, 10, 85), (100, "sensor_error"))
        self.assertEqual(c.target_percent(fan, 30, None, 3000, 10, 85), (100, "sensor_error"))
        self.assertEqual(c.target_percent(fan, 30, 85, 3000, 10, 85), (100, "emergency"))
        self.assertEqual(c.target_percent(fan, 30, 65, 0, 10, 85), (100, "fan_error"))
        self.assertEqual(c.target_percent(fan, 30, 65, 0, 2, 85), (40, "curve"))

    def test_hysteresis_increases_immediately_and_recovers_after_missing_sensor(self):
        h = c.Hysteresis()
        self.assertEqual(h.apply(70, 60, 2), 70)
        self.assertEqual(h.apply(69, 59, 2), 70)
        self.assertEqual(h.apply(67, 58, 2), 67)
        self.assertEqual(h.apply(80, 65, 2), 80)
        self.assertEqual(h.apply(100, None, 2, forced=True), 100)
        self.assertEqual(h.apply(70, 60, 2), 70)

    def test_invalid_curves_are_rejected_without_normalization(self):
        for points in [[{"temp": 40, "percent": 40}],
                       [{"temp": 40, "percent": 40}, {"temp": 40, "percent": 100}],
                       [{"temp": 40, "percent": 70}, {"temp": 80, "percent": 60}],
                       [{"temp": 40, "percent": 0}, {"temp": 80, "percent": 100}],
                       [{"temp": 40, "percent": 40}, {"temp": 80, "percent": 99}]]:
            cfg = configuration()
            cfg["fans"]["cpu"]["points"] = points
            with self.subTest(points=points), self.assertRaises(ValueError):
                c.validate_config(cfg)
        cfg = configuration()
        cfg["emergency_c"] = float("nan")
        with self.assertRaises(ValueError):
            c.validate_config(cfg)

    def test_unknown_sensor_does_not_change_saved_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('{"previous": true}')
            hardware = mock.Mock()
            hardware.sensors.return_value = []
            with mock.patch.object(c, "CONFIG", config), self.assertRaises(ValueError):
                c.api({"action": "save", "config": configuration()}, hardware)
            self.assertEqual(config.read_text(), '{"previous": true}')

    def test_sensor_identity_survives_hwmon_renumbering(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            physical = base / "devices/platform/coretemp.0/hwmon/hwmon9"
            physical.mkdir(parents=True)
            (physical / "name").write_text("coretemp")
            (physical / "temp1_input").write_text("65000")
            root = base / "class/hwmon"
            root.mkdir(parents=True)
            (root / "hwmon9").symlink_to(physical)
            hardware = c.Hardware(root)
            previous = hardware.sensors()[0]["id"]
            (root / "hwmon9").unlink()
            physical.rename(physical.with_name("hwmon12"))
            (root / "hwmon12").symlink_to(physical.with_name("hwmon12"))
            self.assertEqual(hardware.sensors()[0]["id"], previous)


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.hardware = FakeHardware(self.directory)
        self.lock = self.directory / "lock"

    def tearDown(self):
        self.temporary.cleanup()

    def assert_original(self):
        for key, start in [("cpu", 95), ("system", 80)]:
            self.assertEqual(self.hardware.read(self.hardware.attribute(key, "_enable")), 2)
            self.assertEqual(self.hardware.read(self.hardware.attribute(key, "_auto_start")), start)

    def test_stop_restores_shared_register_and_only_controlled_fan(self):
        session = c.ProtectedSession(self.hardware, ["cpu"], self.lock)
        self.hardware.set_percent("cpu", 100)
        self.assertEqual(self.hardware.read(self.hardware.attribute("cpu", "_auto_start")), 255)
        self.assertEqual(self.hardware.read(self.hardware.attribute("system", "_auto_start")), 80)
        session.close()
        self.assert_original()

    def test_competing_controller_cannot_take_lock(self):
        session = c.ProtectedSession(self.hardware, ["cpu"], self.lock)
        try:
            with self.assertRaises(BlockingIOError):
                c.ProtectedSession(self.hardware, ["system"], self.lock)
        finally:
            session.close()

    def test_dead_guardian_falls_back_to_direct_restoration(self):
        session = c.ProtectedSession(self.hardware, list(c.FANS), self.lock)
        self.hardware.set_percent("cpu", 100)
        self.hardware.set_percent("system", 100)
        os.kill(session.guardian, signal.SIGKILL)
        session.close()
        self.assert_original()

    def start_child(self, stalled=False):
        script = """import os,signal,sys,time
from pathlib import Path
import control as c
from test_control import FakeHardware
c.WATCHDOG_TIMEOUT = 1
h = FakeHardware(sys.argv[1])
s = c.ProtectedSession(h, list(c.FANS), Path(sys.argv[1])/'lock')
h.set_percent('cpu', 100)
h.set_percent('system', 100)
print('READY', flush=True)
if sys.argv[2] == 'stall': os.kill(os.getpid(), signal.SIGSTOP)
time.sleep(20)
"""
        child = subprocess.Popen([sys.executable, "-c", script, str(self.directory), "stall" if stalled else "run"],
                                 cwd=str(Path(__file__).parent), stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "READY")
        return child

    def await_restoration(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all(self.hardware.read(self.hardware.attribute(key, "_auto_start")) == start and
                   self.hardware.read(self.hardware.attribute(key, "_enable")) == 2
                   for key, start in [("cpu", 95), ("system", 80)]):
                self.assert_original()
                return
            time.sleep(0.05)
        self.fail("Guardian did not restore original automatic configuration")

    def test_sigkill_of_controller_restores_hardware(self):
        child = self.start_child()
        try:
            child.kill()
            child.wait(timeout=3)
            self.await_restoration()
        finally:
            if child.poll() is None: child.kill()
            child.stdout.close()

    def test_stalled_controller_is_killed_and_restored(self):
        child = self.start_child(stalled=True)
        try:
            child.wait(timeout=3)
            self.assertEqual(child.returncode, -signal.SIGKILL)
            self.await_restoration()
        finally:
            if child.poll() is None: child.kill()
            child.stdout.close()


class TestModeTests(unittest.TestCase):
    def test_protection_overrides_fixed_pwm(self):
        fan = configuration()["fans"]["cpu"]
        for selected, cpu, rpm, expected in [(50, 60, 3000, (80, "test")),
                                             (None, 60, 3000, (100, "sensor_error")),
                                             (50, None, 3000, (100, "sensor_error")),
                                             (50, 90, 3000, (100, "emergency")),
                                             (50, 60, 0, (100, "fan_error"))]:
            with self.subTest(expected=expected):
                self.assertEqual(c.target_percent(fan, selected, cpu, rpm, 10, 85, 80), expected)

    def test_api_validation_timeout_and_stop_preserve_config(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            hardware = FakeHardware(run)
            sensor = {"id": "a" * 24, "celsius": 50}
            config = configuration()
            config["fans"]["cpu"]["enabled"] = False
            path = run / "config.json"
            c.atomic_json(path, config)
            original = path.read_bytes()
            with mock.patch.object(c, "RUN", run), mock.patch.object(c, "CONFIG", path), \
                 mock.patch.object(c, "start_service"), mock.patch.object(c, "daemon_pid", return_value=123), \
                 mock.patch.object(hardware, "sensors", return_value=[sensor]):
                for values in [{"fan": "other", "percent": 80, "duration": 60},
                               {"fan": "cpu", "percent": 19, "duration": 60},
                               {"fan": "cpu", "percent": True, "duration": 60},
                               {"fan": "cpu", "percent": 80, "duration": 301},
                               {"fan": "cpu", "percent": 80, "duration": 0}]:
                    with self.subTest(values=values), self.assertRaises(ValueError):
                        c.api({"action": "test_start", "test": values}, hardware)
                    self.assertFalse((run / "test.json").exists())
                state = c.api({"action": "test_start", "test": {"fan": "cpu", "percent": 80, "duration": 60}}, hardware)
                self.assertEqual(state["test"]["percent"], 80)
                self.assertFalse(state["config"]["fans"]["cpu"]["enabled"])
                with mock.patch.object(c.time, "monotonic", return_value=state["test"]["expires_mono"] + 1):
                    self.assertIsNone(c.active_test())
                c.api({"action": "test_stop"}, hardware)
                self.assertIsNone(c.active_test())
                self.assertEqual(path.read_bytes(), original)

    def test_hardware_avoids_rewriting_unchanged_mode_and_duty(self):
        with tempfile.TemporaryDirectory() as directory:
            hardware = FakeHardware(directory)
            hardware.set_percent("cpu", 80)
            with mock.patch.object(hardware, "write", wraps=hardware.write) as write:
                hardware.set_percent("cpu", 80)
                write.assert_not_called()
                hardware.set_percent("cpu", 75)
                self.assertEqual(write.call_count, 1)
                self.assertEqual(write.call_args.args[0].name, "pwm2")

    def test_trace_is_bounded_and_partial_line_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(c, "RUN", Path(directory)), \
             mock.patch.object(c, "TRACE_LIMIT", 20):
            for n in range(20):
                c.record_sample({"time": n})
            with (Path(directory) / "trace.jsonl").open("a") as handle:
                handle.write('{"unfinished":')
            self.assertEqual(c.diagnostic_samples()[-1], {"time": 19})
            self.assertLess(len(c.diagnostic_samples()), 20)


class DaemonTestModeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.hardware = FakeHardware(self.directory)
        core = self.hardware.root / "hwmon1"
        core.mkdir()
        (core / "name").write_text("coretemp")
        self.temp_path = core / "temp1_input"
        self.temp_path.write_text("50000")
        self.config = configuration()
        sensor_id = self.hardware.sensors()[0]["id"]
        for fan in self.config["fans"].values():
            fan["sensor"] = sensor_id
        self.config["fans"]["cpu"]["enabled"] = False
        self.config_path = self.directory / "config.json"
        c.atomic_json(self.config_path, self.config)
        self.original = self.config_path.read_bytes()
        # Pending tests from a previous daemon must be discarded at startup.
        self.write_test("cpu", 90, 10)
        script = """from pathlib import Path
import sys
import control as c
from test_control import FakeHardware
root = Path(sys.argv[1])
c.RUN = root
c.CONFIG = root/'config.json'
c.INTERVAL = 0.05
c.Hardware = lambda: FakeHardware(root)
session = c.ProtectedSession
c.ProtectedSession = lambda hardware,keys: session(hardware,keys,root/'hardware.lock')
c.daemon()
"""
        self.child = subprocess.Popen([sys.executable, "-c", script, str(self.directory)],
                                      cwd=str(Path(__file__).parent), stderr=subprocess.PIPE, text=True)
        self.wait_for(lambda state: state.get("targets", {}).get("system", {}).get("reason") == "curve")
        self.assertFalse((self.directory / "test.json").exists())

    def tearDown(self):
        if self.child.poll() is None:
            self.child.terminate()
        try:
            self.child.wait(timeout=4)
        finally:
            if self.child.poll() is None:
                self.child.kill()
                self.child.wait(timeout=3)
            self.child.stderr.close()
            self.temporary.cleanup()

    def write_test(self, fan, percent, duration):
        c.atomic_json(self.directory / "test.json", {"fan": fan, "percent": percent,
                      "expires_mono": time.monotonic() + duration})

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            state = c.read_json(self.directory / "state.json", {})
            if predicate(state):
                return state
            if self.child.poll() is not None:
                self.fail("Daemon exited: " + self.child.stderr.read())
            time.sleep(0.02)
        self.fail("Daemon state timeout: " + json.dumps(state))

    def test_expiry_restores_auto_and_other_fan_curve(self):
        self.write_test("cpu", 80, 0.8)
        state = self.wait_for(lambda s: s.get("targets", {}).get("cpu", {}).get("reason") == "test")
        self.assertEqual(state["targets"]["system"]["reason"], "curve")
        self.assertEqual(self.hardware.read(self.hardware.attribute("cpu", "")), 204)
        self.wait_for(lambda s: "cpu" not in s.get("targets", {}) and "system" in s.get("targets", {}))
        self.assertEqual(self.hardware.read(self.hardware.attribute("cpu", "_enable")), 2)
        self.assertEqual(self.hardware.read(self.hardware.attribute("cpu", "_auto_start")), 95)
        self.assertEqual(self.config_path.read_bytes(), self.original)

    def test_update_guard_stop_and_resume_enabled_curve(self):
        self.write_test("system", 80, 10)
        self.wait_for(lambda s: s.get("targets", {}).get("system", {}).get("percent") == 80)
        self.write_test("system", 60, 10)
        self.wait_for(lambda s: s.get("targets", {}).get("system", {}).get("percent") == 60)
        self.temp_path.write_text("90000")
        self.wait_for(lambda s: s.get("targets", {}).get("system", {}).get("reason") == "emergency")
        self.temp_path.write_text("50000")
        self.wait_for(lambda s: s.get("targets", {}).get("system", {}).get("reason") == "test")
        (self.directory / "test.json").unlink()
        self.wait_for(lambda s: s.get("targets", {}).get("system", {}).get("reason") == "curve")
        self.assertEqual(self.config_path.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
