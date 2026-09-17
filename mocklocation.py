#!/usr/bin/env python3
"""Bridge a phone's (usually spoofed) location to MQTT, live, over adb.

Reads whatever location Android currently holds -- including one set by a mock
location app such as Fake GPS Location -- and republishes every new fix as a
flat JSON message.

  mocklocation.py [-s SERIAL] [-b BROKER] [-t TOPIC] [--dry-run]

Nothing is installed on the phone: this only needs USB debugging, and the mock
app selected under Developer options -> Select mock location app.

Security note: a password passed as --password ends up in your shell history
and in `ps` output. Pass --password with no value to be prompted for it
instead, or put it in MQTT_PASSWORD.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(SCRIPT_DIR, ".venv")
BOOTSTRAP_FLAG = "MOCKLOC_BOOTSTRAPPED"


def _bootstrap():
    """Create a local venv with paho-mqtt and re-exec into it."""
    if os.environ.get(BOOTSTRAP_FLAG):
        sys.exit(
            "paho-mqtt is still missing after bootstrapping. Try deleting\n"
            "  %s\nand running again." % VENV_DIR
        )

    import subprocess

    venv_python = os.path.join(VENV_DIR, "bin", "python")
    if not os.path.exists(venv_python):
        print("First run: creating %s and installing paho-mqtt..." % VENV_DIR,
              file=sys.stderr)
        import venv

        try:
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        except Exception as exc:
            sys.exit("Could not create the virtualenv: %s" % exc)

    try:
        subprocess.check_call(
            [venv_python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"]
        )
        subprocess.check_call(
            [venv_python, "-m", "pip", "install", "--quiet", "paho-mqtt>=2"]
        )
    except subprocess.CalledProcessError:
        sys.exit(
            "Installing paho-mqtt failed (see the pip output above).\n"
            "You can retry after deleting %s" % VENV_DIR
        )

    env = dict(os.environ, **{BOOTSTRAP_FLAG: "1"})
    os.execve(venv_python, [venv_python, os.path.abspath(__file__)] + sys.argv[1:], env)


try:
    import paho.mqtt.client as mqtt
except ImportError:
    _bootstrap()

import argparse
import datetime
import getpass
import json
import math
import re
import shutil
import signal
import subprocess
import threading
import time


ADB = os.environ.get("ADB", "adb")

# Every Android version prints fixes the same way inside dumpsys, whether under
# "Last Known Locations:" (10+) or "last location=" (9 and older).
LOCATION_RE = re.compile(r"Location\[([a-z]+)\s+(-?\d+\.\d+),(-?\d+\.\d+)([^\]]*)\]")
# Bare "lat, lon" pairs, for --source logcat where there is no Location[...] wrapper.
LATLON_RE = re.compile(r"(-?\d{1,3}\.\d{4,})\s*,\s*(-?\d{1,3}\.\d{4,})")
KV_RE = re.compile(r"([A-Za-z]+)=(-?\d+(?:\.\d+)?)(?![A-Za-z])")
# et= is the device's own clock since boot: "+2h3m4s500ms", or bare millis.
ET_TOKEN_RE = re.compile(r"\bet=(\S+)")
ET_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)?")
ET_UNIT_MS = {"ms": 1, "s": 1000, "m": 60000, "h": 3600000, "d": 86400000}

PROVIDER_RANK = {"fused": 0, "gps": 1, "network": 2, "passive": 3}
STALE_AFTER = 10.0   # seconds without a new fix before we say something
MIN_MOVE = 0.5       # metres; below this we don't bother deriving speed
MAX_SPEED = 1000.0   # m/s; anything faster came from a bogus time delta
CONNECT_TIMEOUT = 10.0  # seconds to wait for the broker's CONNACK

INTERACTIVE = sys.stdin.isatty()
PROMPT_FOR_IT = "\0prompt"  # sentinel for `--password` with no value


def die(message, code=1):
    sys.exit("error: %s" % message if code == 1 else message)


def warn(message):
    print("warning: %s" % message, file=sys.stderr)


def need_tty(what):
    """Fail loudly instead of hanging when there is no tty to prompt on."""
    if not INTERACTIVE:
        die("no %s given and stdin is not a terminal, so I can't ask for it" % what)


def resolve_credentials(args):
    """Settle username/password from flags, then the environment, then a prompt."""
    args.username = args.username or os.environ.get("MQTT_USERNAME")

    if args.password == PROMPT_FOR_IT:
        need_tty("--password")
        args.password = getpass.getpass(
            "Password for %s: " % (args.username or "the broker"))
    elif args.password is None:
        args.password = os.environ.get("MQTT_PASSWORD")

    # MQTT 3.1.1 has no way to send a password without a username.
    if args.password and not args.username:
        die("a --password needs a --username (or set MQTT_USERNAME)")


def adb_devices():
    """Return [(serial, state)] as adb sees them right now."""
    if shutil.which(ADB) is None:
        die("adb not found on PATH (brew install --cask android-platform-tools)")
    try:
        out = subprocess.run([ADB, "devices"], capture_output=True, text=True,
                             timeout=20).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        die("could not run adb: %s" % exc)
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def pick_device(requested):
    """Resolve the serial to use, explaining clearly when we can't."""
    devices = adb_devices()
    if requested:
        for serial, state in devices:
            if serial == requested:
                if state != "device":
                    die("%s is in state '%s', not ready" % (serial, state))
                return serial
        die("no device with serial %s (adb devices shows: %s)"
            % (requested, ", ".join(s for s, _ in devices) or "nothing"))

    ready = [s for s, state in devices if state == "device"]
    if not ready:
        unauthorized = [s for s, state in devices if state == "unauthorized"]
        if unauthorized:
            die("device %s is unauthorized -- accept the USB debugging prompt "
                "on the phone" % unauthorized[0])
        die("no device attached; enable USB debugging and check `adb devices`")
    if len(ready) > 1:
        die("several devices attached (%s); pick one with --device"
            % ", ".join(ready))
    return ready[0]


def parse_et(token):
    """Turn an et= value ("+2h3m4s500ms", or bare millis) into milliseconds."""
    sign = -1 if token.startswith("-") else 1
    total = 0.0
    matched = False
    for value, unit in ET_PART_RE.findall(token):
        matched = True
        total += float(value) * ET_UNIT_MS.get(unit, 1)
    return sign * total if matched else None


def parse_location(line):
    """Pull a fix out of a dumpsys line. Returns a dict or None."""
    match = LOCATION_RE.search(line)
    if not match:
        return None
    provider, lat, lon, tail = match.groups()
    raw = match.group(0)
    fix = {"provider": provider, "lat": float(lat), "lon": float(lon),
           "raw": raw,
           # et ticks on every refresh even when parked, so the "has this
           # changed?" key is the fix with et stripped back out.
           "key": ET_TOKEN_RE.sub("", raw)}
    for key, value in KV_RE.findall(tail):
        fix[key] = float(value)
    et = ET_TOKEN_RE.search(tail)
    if et:
        fix["et_ms"] = parse_et(et.group(1))
    return fix


def parse_logcat(line):
    """Pull a bare 'lat, lon' pair out of a logcat line. Returns a dict or None."""
    match = LATLON_RE.search(line)
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"provider": "logcat", "lat": lat, "lon": lon,
            "raw": match.group(0), "key": match.group(0)}


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    radius = 6371008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a))


def initial_bearing(lat1, lon1, lat2, lon2):
    """Compass bearing in degrees from the first point to the second."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def build_payload(fix, previous, device_id, now):
    """Flat JSON message: the fix, plus speed/bearing derived when missing."""
    payload = {"lat": round(fix["lat"], 7), "lon": round(fix["lon"], 7)}

    if "alt" in fix:
        payload["alt"] = fix["alt"]
    if "hAcc" in fix:
        payload["accuracy"] = fix["hAcc"]

    speed = fix.get("vel")
    bearing = fix.get("bear")
    if (speed is None or bearing is None) and previous is not None:
        distance = haversine(previous["lat"], previous["lon"],
                             fix["lat"], fix["lon"])
        # The device's own clock beats ours: two fixes can reach us in the same
        # millisecond, which would turn a 20 m step into a warp-speed reading.
        if fix.get("et_ms") is not None and previous.get("et_ms") is not None:
            elapsed = (fix["et_ms"] - previous["et_ms"]) / 1000.0
        else:
            elapsed = now - previous["at"]
        if elapsed > 0 and distance > MIN_MOVE:
            if bearing is None:
                bearing = initial_bearing(previous["lat"], previous["lon"],
                                          fix["lat"], fix["lon"])
            if speed is None and distance / elapsed < MAX_SPEED:
                speed = distance / elapsed
    if speed is not None:
        payload["speed"] = round(speed, 2)
    if bearing is not None:
        payload["bearing"] = round(bearing, 1)

    payload["provider"] = fix["provider"]
    payload["device"] = device_id
    payload["ts"] = round(now, 3)
    payload["time"] = (datetime.datetime.utcfromtimestamp(now)
                       .strftime("%Y-%m-%dT%H:%M:%SZ"))
    return payload


def best_fix(lines, wanted):
    """Of the fixes in these lines, the one for the provider we want."""
    fixes = [f for f in (parse_location(line) for line in lines) if f]
    if not fixes:
        return None
    if wanted != "any":
        for fix in fixes:
            if fix["provider"] == wanted:
                return fix
        return None
    return min(fixes, key=lambda f: PROVIDER_RANK.get(f["provider"], 9))


def stream_dumpsys(serial, interval, verbose):
    """Yield dumpsys lines from one long-lived shell loop on the device.

    Keeping the loop on the phone means each fix costs no adb round trip, which
    is what makes this keep up with a route in real time.
    """
    script = ('while true; do dumpsys location | grep -o "Location\\[[^]]*\\]"; '
              'echo "--"; sleep %s; done' % interval)
    proc = subprocess.Popen([ADB, "-s", serial, "shell", script],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    if verbose:
        print("streaming dumpsys from %s every %ss" % (serial, interval),
              file=sys.stderr)

    batch = []
    saw_output = False
    try:
        for line in proc.stdout:
            line = line.strip()
            if line == "--":
                saw_output = True
                yield batch
                batch = []
            elif line:
                batch.append(line)
    finally:
        proc.terminate()
        if not saw_output:
            # Old shells reject a fractional sleep; say so rather than dying mute.
            stderr = ""
            try:
                stderr = proc.stderr.read() or ""
            except Exception:
                pass
            if stderr.strip():
                warn("device shell said: %s" % stderr.strip().splitlines()[0])


def poll_dumpsys(serial, interval, verbose):
    """Fallback: one `adb shell dumpsys location` per tick, from the host."""
    if verbose:
        print("polling dumpsys from %s every %ss" % (serial, interval),
              file=sys.stderr)
    while True:
        try:
            out = subprocess.run([ADB, "-s", serial, "shell", "dumpsys", "location"],
                                 capture_output=True, text=True, timeout=20).stdout
        except subprocess.TimeoutExpired:
            warn("dumpsys timed out; retrying")
            out = ""
        except (OSError, subprocess.SubprocessError) as exc:
            die("adb failed: %s" % exc)
        yield [l for l in out.splitlines() if "Location[" in l]
        time.sleep(interval)


def stream_logcat(serial, verbose):
    """Yield fixes regexed out of logcat, for apps that log their coordinates."""
    if verbose:
        print("tailing logcat on %s" % serial, file=sys.stderr)
    proc = subprocess.Popen([ADB, "-s", serial, "logcat", "-v", "brief"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1)
    try:
        for line in proc.stdout:
            fix = parse_location(line) or parse_logcat(line)
            if fix:
                yield fix
    finally:
        proc.terminate()


def fixes_from_file(path, provider):
    """Parse captured dumpsys text, so parsing can be checked with no phone."""
    try:
        with open(path) as handle:
            text = handle.read()
    except OSError as exc:
        die("can't read %s: %s" % (path, exc))
    fixes = [f for f in (parse_location(l) for l in text.splitlines()) if f]
    if provider == "any" and fixes:
        # A capture interleaves every provider; live mode only ever follows
        # one, so pick the best one present and stay on it.
        provider = min((f["provider"] for f in fixes),
                       key=lambda p: PROVIDER_RANK.get(p, 9))
    return [f for f in fixes if f["provider"] == provider]


class Publisher(object):
    """Either an MQTT connection, or stdout when --dry-run."""

    def __init__(self, args, topic):
        self.topic = topic
        self.qos = args.qos
        self.retain = args.retain
        self.verbose = args.verbose
        self.status_topic = args.status_topic
        self.client = None
        self.closing = False
        self.established = False
        self.connected = threading.Event()
        self.reason = None
        if args.dry_run:
            return

        client_id = args.client_id or ("mocklocation-%d" % os.getpid())
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=client_id)
        if args.username:
            self.client.username_pw_set(args.username, args.password)
        if args.tls:
            self.client.tls_set()
        if self.status_topic:
            self.client.will_set(self.status_topic, "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        try:
            self.client.connect(args.broker, args.port, keepalive=30)
        except OSError as exc:
            die("could not connect to %s:%d -- %s" % (args.broker, args.port, exc))
        self.client.loop_start()

        # connect() only finishes the TCP handshake -- a refused login arrives
        # later, as a CONNACK. Without waiting for it we'd cheerfully stream
        # fixes into a connection the broker has already thrown away.
        if not self.connected.wait(CONNECT_TIMEOUT):
            self.client.loop_stop()
            die("no response from %s:%d after %gs -- is that an MQTT broker?"
                % (args.broker, args.port, CONNECT_TIMEOUT))
        if self.reason is not None:
            self.client.loop_stop()
            message = "broker rejected the connection: %s" % self.reason
            if self._looks_like_auth(self.reason):
                message += ("\n       check --username/--password "
                            "(or MQTT_USERNAME/MQTT_PASSWORD)")
            die(message)

        self.established = True
        if self.status_topic:
            self.client.publish(self.status_topic, "online", qos=1, retain=True)

    @staticmethod
    def _looks_like_auth(reason):
        text = str(reason).lower()
        return "user name" in text or "password" in text or "auth" in text

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # paho hands v3 return codes back as ReasonCodes too, so one path does.
        failed = getattr(reason_code, "is_failure", reason_code != 0)
        self.reason = reason_code if failed else None
        if self.verbose:
            print("connected to broker (%s)" % reason_code, file=sys.stderr)
        self.connected.set()

    def _on_disconnect(self, client, userdata, flags=None, reason_code=None,
                       properties=None):
        if self.closing or not self.established:
            # Before the CONNACK check has passed, the caller is about to print
            # a far better message than this one -- don't talk over it.
            return
        # paho reconnects underneath us, which is right -- but say so, or a
        # broker that drops us mid-route looks like the phone stopping.
        warn("disconnected from broker (%s); retrying" % reason_code)

    def publish(self, payload):
        line = json.dumps(payload)
        if self.client is None:
            print(line, flush=True)
            return
        self.client.publish(self.topic, line, qos=self.qos, retain=self.retain)
        if self.verbose:
            print("%s %s" % (self.topic, line), file=sys.stderr)

    def close(self):
        if self.client is None:
            return
        self.closing = True
        if self.status_topic:
            self.client.publish(self.status_topic, "offline", qos=1,
                                retain=True).wait_for_publish(timeout=2)
        self.client.loop_stop()
        self.client.disconnect()


def main():
    parser = argparse.ArgumentParser(
        prog="mocklocation.py",
        description="Publish an Android device's current location to MQTT as it "
                    "changes -- including one spoofed by Fake GPS Location.",
        epilog="The phone needs USB debugging on, and the spoofing app selected "
               "under Developer options -> Select mock location app. Use "
               "--dry-run first to check the phone side without a broker. "
               "A password given as a flag lands in your shell history and in "
               "ps output; pass --password with no value for a hidden prompt, "
               "or set MQTT_PASSWORD.",
    )
    parser.add_argument("-s", "--device", metavar="SERIAL",
                        help="adb serial; only needed with several devices attached")
    parser.add_argument("-b", "--broker", default="localhost",
                        help="MQTT broker host (default: localhost)")
    parser.add_argument("-P", "--port", type=int, default=1883,
                        help="MQTT broker port (default: 1883)")
    parser.add_argument("-t", "--topic", default="gps/{serial}",
                        help="topic to publish on; {serial} and {id} are "
                             "substituted (default: gps/{serial})")
    parser.add_argument("-i", "--interval", type=float, default=0.3,
                        metavar="SECONDS",
                        help="how often to read the device (default: 0.3)")
    parser.add_argument("--provider", default="any",
                        choices=["any", "fused", "gps", "network", "passive"],
                        help="which location provider to trust (default: any, "
                             "preferring fused then gps)")
    parser.add_argument("--source", default="dumpsys",
                        choices=["dumpsys", "logcat"],
                        help="dumpsys reads the device's last known fix (works "
                             "with any mock app); logcat only works if the app "
                             "logs its coordinates")
    parser.add_argument("--qos", type=int, default=0, choices=[0, 1, 2],
                        help="MQTT QoS (default: 0)")
    parser.add_argument("--retain", action="store_true",
                        help="retain each message so late subscribers get the "
                             "last position immediately")
    parser.add_argument("-u", "--username",
                        help="MQTT username (or set MQTT_USERNAME)")
    parser.add_argument("--password", nargs="?", const=PROMPT_FOR_IT,
                        metavar="PASS",
                        help="MQTT password; pass the flag with no value to be "
                             "prompted for it, or set MQTT_PASSWORD")
    parser.add_argument("--tls", action="store_true",
                        help="connect over TLS (port is usually 8883)")
    parser.add_argument("--client-id", help="MQTT client id (default: mocklocation-PID)")
    parser.add_argument("--id", dest="device_id", metavar="NAME",
                        help="name to publish as, instead of the adb serial")
    parser.add_argument("--status-topic",
                        help="publish retained online/offline here, with a "
                             "matching last will")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the JSON instead of connecting to a broker")
    parser.add_argument("--from-file", metavar="PATH",
                        help="parse captured dumpsys text instead of a device "
                             "(for checking the parser with no phone)")
    parser.add_argument("--list-devices", action="store_true",
                        help="list attached devices and exit")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every message and broker event to stderr")
    args = parser.parse_args()

    if args.list_devices:
        devices = adb_devices()
        if not devices:
            die("no devices attached; enable USB debugging and replug")
        for serial, state in devices:
            print("%s\t%s" % (serial, state))
        return 0

    if args.interval <= 0:
        die("--interval must be positive")

    if not args.dry_run:
        resolve_credentials(args)

    if args.from_file:
        serial = "file"
    else:
        serial = pick_device(args.device)
    device_id = args.device_id or serial
    topic = args.topic.format(serial=serial, id=device_id)

    publisher = Publisher(args, topic)
    if args.verbose or not args.dry_run:
        print("%s -> %s" % (device_id, "stdout" if args.dry_run
                            else "%s:%d %s" % (args.broker, args.port, topic)),
              file=sys.stderr)

    previous = None
    last_raw = None
    last_new_fix = time.time()
    warned_stale = False
    count = 0

    def emit(fix):
        """Publish a fix and remember it for the next speed/bearing estimate."""
        nonlocal previous, count
        now = time.time()
        publisher.publish(build_payload(fix, previous, device_id, now))
        previous = {"lat": fix["lat"], "lon": fix["lon"], "at": now,
                    "et_ms": fix.get("et_ms")}
        count += 1

    try:
        if args.from_file:
            for fix in fixes_from_file(args.from_file, args.provider):
                if fix["key"] == last_raw:
                    continue
                last_raw = fix["key"]
                emit(fix)
            if not count:
                die("no Location[...] fixes found in %s" % args.from_file)
        elif args.source == "logcat":
            for fix in stream_logcat(serial, args.verbose):
                if fix["key"] == last_raw:
                    continue
                last_raw = fix["key"]
                emit(fix)
        else:
            batches = stream_dumpsys(serial, args.interval, args.verbose)
            saw_any = False
            for lines in batches:
                if lines:
                    saw_any = True
                fix = best_fix(lines, args.provider)
                if fix is not None and fix["key"] != last_raw:
                    last_raw = fix["key"]
                    last_new_fix = time.time()
                    warned_stale = False
                    emit(fix)
                if not warned_stale and time.time() - last_new_fix > STALE_AFTER:
                    warned_stale = True
                    if saw_any:
                        warn("no new fix for %ds -- is the route still running?"
                             % STALE_AFTER)
                    else:
                        warn("the device reports no location at all -- is Fake "
                             "GPS selected under Developer options -> Select "
                             "mock location app?")
            if not saw_any:
                # The generator ended without ever producing a line: fall back.
                warn("streaming shell gave nothing back; falling back to polling")
                for lines in poll_dumpsys(serial, args.interval, args.verbose):
                    fix = best_fix(lines, args.provider)
                    if fix and fix["key"] != last_raw:
                        last_raw = fix["key"]
                        emit(fix)
    finally:
        publisher.close()
        if count and not args.dry_run:
            print("published %d fixes" % count, file=sys.stderr)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\naborted")
