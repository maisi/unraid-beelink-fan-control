#!/usr/bin/env python3
"""Beelink EQ12 fan curves. Standard library only; compatible with Python 3.9."""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import syslog
import tempfile
import threading
import time

PLUGIN = "beelink-fan-control"
CONFIG = Path("/boot/config/plugins") / PLUGIN / "config.json"
RUN = Path("/var/run") / PLUGIN
SHARED_LOCK = Path("/var/run/beelink-fan.lock")
FANS = {"cpu": (2, "CPU-Lüfter"), "system": (3, "Gehäuselüfter")}
INTERVAL = 2
WATCHDOG_TIMEOUT = 10


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return copy.deepcopy(fallback)


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + " muss eine Zahl sein.")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("%s muss zwischen %s und %s liegen." % (label, low, high))
    return float(value)


def validate_config(config):
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Ungültiges Konfigurationsformat.")
    result = {"version": 1,
              "emergency_c": number(config.get("emergency_c"), 70, 95, "CPU-Notkühlung"),
              "hysteresis_c": number(config.get("hysteresis_c"), 0, 5, "Hysterese"),
              "fans": {}}
    if not isinstance(config.get("fans"), dict) or set(config["fans"]) != set(FANS):
        raise ValueError("Es müssen CPU- und Gehäuselüfter konfiguriert sein.")
    for key, fan in config["fans"].items():
        if not isinstance(fan, dict) or not isinstance(fan.get("enabled"), bool):
            raise ValueError("Ungültiger Betriebsmodus für " + key)
        sensor = fan.get("sensor", "")
        if not isinstance(sensor, str) or not re.fullmatch(r"[a-f0-9]{24}|", sensor):
            raise ValueError("Ungültige Sensorauswahl.")
        if fan["enabled"] and not sensor:
            raise ValueError("Bitte einen Temperatursensor auswählen.")
        points = fan.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= 10:
            raise ValueError("Eine Lüfterkurve braucht 2 bis 10 Punkte.")
        curve = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("Ungültiger Kurvenpunkt.")
            temp = number(point.get("temp"), 0, 100, "Temperatur")
            percent = number(point.get("percent"), 20, 100, "Lüfterleistung")
            if curve and (temp <= curve[-1]["temp"] or percent < curve[-1]["percent"]):
                raise ValueError("Temperaturen müssen steigen; die Lüfterleistung darf nicht sinken.")
            curve.append({"temp": temp, "percent": percent})
        if curve[-1]["percent"] != 100:
            raise ValueError("Der letzte Kurvenpunkt muss 100 % erreichen.")
        result["fans"][key] = {"enabled": fan["enabled"], "sensor": sensor, "points": curve}
    return result


def curve_percent(points, temperature):
    if temperature <= points[0]["temp"]:
        return points[0]["percent"]
    for left, right in zip(points, points[1:]):
        if temperature <= right["temp"]:
            fraction = (temperature - left["temp"]) / (right["temp"] - left["temp"])
            return left["percent"] + fraction * (right["percent"] - left["percent"])
    return points[-1]["percent"]


def target_percent(fan, selected_c, cpu_c, rpm, age, emergency_c):
    if cpu_c is None or selected_c is None:
        return 100.0, "sensor_error"
    if cpu_c >= emergency_c:
        return 100.0, "emergency"
    if age >= 8 and (rpm is None or rpm < 200):
        return 100.0, "fan_error"
    return curve_percent(fan["points"], selected_c), "curve"


class Hysteresis:
    def __init__(self):
        self.percent = None
        self.reference_c = None
        self.forced = False

    def apply(self, requested, temperature, band, forced=False):
        if forced or self.forced or self.percent is None or self.reference_c is None or requested >= self.percent:
            self.percent, self.reference_c = requested, temperature
        elif temperature <= self.reference_c - band:
            self.percent, self.reference_c = requested, temperature
        self.forced = forced
        return self.percent


class Hardware:
    def __init__(self, root=Path("/sys/class/hwmon"), dmi=Path("/sys/class/dmi/id")):
        self.root, self.dmi = Path(root), Path(dmi)

    @staticmethod
    def read(path):
        try:
            return int(Path(path).read_text().strip())
        except (OSError, ValueError):
            return None

    def chips(self):
        for path in sorted(self.root.glob("hwmon*")):
            try:
                yield path, (path / "name").read_text().strip()
            except OSError:
                continue

    @staticmethod
    def identity(path, chip, attribute):
        device = re.sub(r"/hwmon(?:/hwmon\d+|\d+)$", "", str(path.resolve()))
        return hashlib.sha256((chip + "|" + device + "|" + attribute).encode()).hexdigest()[:24]

    def sensors(self):
        result = []
        for path, chip in self.chips():
            for sensor in sorted(path.glob("temp*_input")):
                label_file = sensor.with_name(sensor.name.replace("_input", "_label"))
                try:
                    label = label_file.read_text().strip()
                except OSError:
                    label = sensor.name.replace("_input", "")
                raw = self.read(sensor)
                temperature = raw / 1000.0 if raw is not None and -40000 <= raw <= 150000 else None
                if chip == "coretemp" and sensor.name == "temp1_input":
                    label = "CPU-Paket"
                elif chip == "nvme":
                    label = "NVMe · " + label
                elif chip == "it8613":
                    label = "Mainboard · " + label
                elif chip == "acpitz":
                    label = "ACPI · " + label
                result.append({"id": self.identity(path, chip, sensor.name), "label": label,
                               "chip": chip, "channel": sensor.name, "celsius": temperature,
                               "path": str(sensor)})
        return result

    def fan_chip(self):
        matches = [path for path, chip in self.chips() if chip == "it8613"]
        if len(matches) != 1:
            raise RuntimeError("IT8613-Treiber fehlt oder der Chip ist nicht eindeutig.")
        return matches[0]

    def check_board(self):
        if (self.dmi / "board_vendor").read_text().strip() != "AZW" or (self.dmi / "board_name").read_text().strip() != "EQ":
            raise RuntimeError("Dieses Plugin steuert ausschließlich das AZW-EQ-Board.")

    def attribute(self, key, suffix):
        channel = FANS[key][0]
        return self.fan_chip() / ("pwm%d%s" % (channel, suffix))

    def write(self, path, value):
        Path(path).write_text(str(value) + "\n")

    def capture(self, keys):
        self.check_board()
        original = {}
        for key in keys:
            mode = self.read(self.attribute(key, "_enable"))
            start = self.read(self.attribute(key, "_auto_start"))
            if mode != 2 or start is None:
                raise RuntimeError(FANS[key][1] + ": Ein anderer Regler ist aktiv oder Automatik fehlt.")
            original[key] = {"mode": mode, "start": start}
        return original

    def restore(self, original):
        failures = []
        for key, saved in original.items():
            try:
                self.write(self.attribute(key, "_enable"), saved["mode"])
                self.write(self.attribute(key, "_auto_start"), saved["start"])
            except OSError as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError("Automatik konnte nicht vollständig restauriert werden: " + "; ".join(failures))

    def set_percent(self, key, percent):
        mode = self.attribute(key, "_enable")
        if percent >= 100:
            self.write(mode, 0)  # IT8613: atomically enter full speed.
        else:
            if self.read(mode) == 2:
                self.write(mode, 0)  # Full speed before taking manual control.
            self.write(mode, 1)
            self.write(self.attribute(key, ""), int(percent * 255 / 100 + 0.5))

    def fan_state(self):
        result = {}
        try:
            chip = self.fan_chip()
        except RuntimeError:
            chip = None
        for key, (channel, label) in FANS.items():
            mode = self.read(chip / ("pwm%d_enable" % channel)) if chip else None
            pwm = self.read(chip / ("pwm%d" % channel)) if chip else None
            result[key] = {"label": label, "available": chip is not None,
                           "rpm": self.read(chip / ("fan%d_input" % channel)) if chip else None,
                           "mode": mode, "percent": round(pwm * 100 / 255, 1) if mode in (0, 1) and pwm is not None else None}
        return result


def default_config(hardware):
    sensors = hardware.sensors()
    cpu = next((s["id"] for s in sensors if s["chip"] == "coretemp" and s["channel"] == "temp1_input"), "")
    system = next((s["id"] for s in sensors if s["chip"] == "nvme" and s["channel"] == "temp1_input"), cpu)
    points = lambda pairs: [{"temp": temp, "percent": percent} for temp, percent in pairs]
    return {"version": 1, "emergency_c": 85, "hysteresis_c": 2, "fans": {
        "cpu": {"enabled": False, "sensor": cpu, "points": points([(35, 35), (50, 45), (65, 65), (75, 85), (85, 100)])},
        "system": {"enabled": False, "sensor": system, "points": points([(30, 35), (40, 45), (50, 70), (60, 100)])}}}


class ProtectedSession:
    """A separate guardian retains the hardware lock and restores after EOF/hang."""
    def __init__(self, hardware, keys, lock=SHARED_LOCK):
        self.hardware = hardware
        self.lock = open(lock, "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.original = hardware.capture(keys)
            reader, writer = os.pipe()
            parent = os.getpid()
            self.guardian = os.fork()
            if self.guardian == 0:
                os.close(writer)
                for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    signal.signal(signum, signal.SIG_IGN)
                success = True
                try:
                    while True:
                        ready, _, _ = select.select([reader], [], [], WATCHDOG_TIMEOUT)
                        if not ready:
                            syslog.syslog(syslog.LOG_ERR, "Fan controller stalled; restoring hardware automatic mode")
                            try:
                                os.kill(parent, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            break
                        if not os.read(reader, 4096):
                            break
                    hardware.restore(self.original)
                except Exception as error:
                    syslog.syslog(syslog.LOG_ERR, "Fan guardian: " + str(error))
                    success = False
                finally:
                    os.close(reader)
                    self.lock.close()
                os._exit(0 if success else 1)
            os.close(reader)
            self.writer = writer
            self.closed = False
            self.started = time.monotonic()
            self.pulse()
        except BaseException:
            self.lock.close()
            raise

    def pulse(self):
        os.write(self.writer, b".")

    def close(self):
        if self.closed:
            return
        self.closed = True
        os.close(self.writer)
        status = None
        for _ in range(50):
            pid, result = os.waitpid(self.guardian, os.WNOHANG)
            if pid:
                status = result
                break
            time.sleep(0.05)
        if status is None:
            os.kill(self.guardian, signal.SIGKILL)
            _, status = os.waitpid(self.guardian, 0)
        try:
            if status != 0:
                self.hardware.restore(self.original)
        finally:
            self.lock.close()


def daemon_pid():
    try:
        pid = int((RUN / "daemon.pid").read_text())
        command = Path("/proc/%d/cmdline" % pid).read_bytes().split(b"\0")
        if b"daemon" in command and str(Path(__file__).resolve()).encode() in command:
            return pid
    except (OSError, ValueError):
        pass
    return None


def start_service():
    if daemon_pid() is not None:
        return
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "daemon"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)
    for _ in range(30):
        if daemon_pid() is not None:
            return
        time.sleep(0.1)
    raise RuntimeError("Lüfterdienst konnte nicht gestartet werden.")


def stop_service():
    pid = daemon_pid()
    if pid is not None:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if daemon_pid() is None:
                return
            time.sleep(0.1)
        raise RuntimeError("Lüfterdienst reagiert nicht; der Schutzprozess überwacht die Rückkehr zur Automatik.")


def load_config(hardware):
    if not CONFIG.exists():
        return validate_config(default_config(hardware))
    return validate_config(json.loads(CONFIG.read_text()))


def daemon():
    RUN.mkdir(parents=True, exist_ok=True)
    service_lock = open(RUN / "service.lock", "a")
    try:
        fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    (RUN / "daemon.pid").write_text(str(os.getpid()))
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: stop.set())
    hardware = Hardware()
    session = None
    active_config = None
    controllers = {}
    previous_error = None
    try:
        while not stop.is_set():
            error = None
            targets = {}
            try:
                config = load_config(hardware)
                if config != active_config:
                    if session:
                        session.close()
                        session = None
                    active_config = config
                    controllers = {key: Hysteresis() for key in FANS}
                keys = [key for key in FANS if config["fans"][key]["enabled"]]
                if keys and session is None:
                    session = ProtectedSession(hardware, keys)
                if session:
                    session.pulse()
                    sensors = hardware.sensors()
                    by_id = {sensor["id"]: sensor for sensor in sensors}
                    cpu_c = next((s["celsius"] for s in sensors if s["chip"] == "coretemp" and s["channel"] == "temp1_input"), None)
                    states = hardware.fan_state()
                    for key in keys:
                        fan = config["fans"][key]
                        selected_c = by_id.get(fan["sensor"], {}).get("celsius")
                        requested, reason = target_percent(fan, selected_c, cpu_c, states[key]["rpm"],
                                                           time.monotonic() - session.started, config["emergency_c"])
                        percent = controllers[key].apply(requested, selected_c if selected_c is not None else cpu_c,
                                                         config["hysteresis_c"], reason != "curve")
                        hardware.set_percent(key, percent)
                        targets[key] = {"percent": round(percent, 1), "reason": reason, "sensor_c": selected_c}
                    session.pulse()
            except Exception as failure:
                error = "Ein anderer Lüfterregler oder Boost ist aktiv; die eigene Kurve wartet." if isinstance(failure, BlockingIOError) else str(failure)
                if session:
                    try:
                        session.close()
                    except Exception as restore_error:
                        error += "; " + str(restore_error)
                    session = None
            if error != previous_error:
                if error:
                    syslog.syslog(syslog.LOG_ERR, PLUGIN + ": " + error)
                previous_error = error
            atomic_json(RUN / "state.json", {"updated": time.time(), "error": error, "targets": targets})
            stop.wait(INTERVAL)
    finally:
        if session:
            session.close()
        (RUN / "daemon.pid").unlink(missing_ok=True)
        atomic_json(RUN / "state.json", {"updated": time.time(), "error": None, "targets": {}})
        service_lock.close()


def snapshot(hardware):
    sensors = hardware.sensors()
    for sensor in sensors:
        sensor.pop("path", None)
    return {"ok": True, "config": load_config(hardware), "sensors": sensors,
            "fans": hardware.fan_state(), "service": {"running": daemon_pid() is not None,
            **read_json(RUN / "state.json", {"updated": None, "error": None, "targets": {}})}}


def api(request, hardware):
    if not isinstance(request, dict):
        raise ValueError("Ungültige Anfrage.")
    action = request.get("action", "status")
    if action == "save":
        config = validate_config(request.get("config"))
        known = {sensor["id"] for sensor in hardware.sensors() if sensor["celsius"] is not None}
        for fan in config["fans"].values():
            if fan["enabled"] and fan["sensor"] not in known:
                raise ValueError("Der ausgewählte Temperatursensor ist derzeit nicht verfügbar.")
        if any(fan["enabled"] for fan in config["fans"].values()):
            hardware.check_board()
            hardware.fan_chip()
        start_service()
        atomic_json(CONFIG, config)
    elif action == "auto":
        config = load_config(hardware)
        for fan in config["fans"].values():
            fan["enabled"] = False
        atomic_json(CONFIG, config)
        start_service()
    elif action != "status":
        raise ValueError("Unbekannte Aktion.")
    return snapshot(hardware)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "daemon", "start", "stop", "status", "api"])
    args = parser.parse_args()
    try:
        hardware = Hardware()
        if args.command == "init":
            if not CONFIG.exists():
                atomic_json(CONFIG, validate_config(default_config(hardware)))
        elif args.command == "daemon":
            daemon()
        elif args.command == "start":
            start_service()
        elif args.command == "stop":
            stop_service()
        else:
            request = json.load(sys.stdin) if args.command == "api" else {"action": "status"}
            print(json.dumps(api(request, hardware), ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
