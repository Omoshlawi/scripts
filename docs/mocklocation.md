# mocklocation.py

Reads whatever location an Android device currently holds — including one spoofed by
**Fake GPS Location** driving a route — and republishes every new fix to an MQTT topic
as flat JSON, live. Nothing is installed on the phone and nothing is written to disk.

```sh
./mocklocation.py --dry-run                              # check the phone side first
./mocklocation.py -b 192.168.1.10 -t fleet/car1/position # publish to your broker
```

## First run

```
First run: creating /Users/you/projects/scripts/.venv and installing paho-mqtt...
```

Only the first invocation is slow; after that the script re-execs into `.venv/` instantly.

## Setting up the phone

1. Developer options → **USB debugging** on, plugged in, and the *Allow USB debugging?*
   prompt accepted. `./mocklocation.py --list-devices` should show the serial as `device`.
2. Developer options → **Select mock location app** → *Fake GPS Location*.
3. Start a route (or drop a pin) in Fake GPS, then run the bridge.

## Options

| Flag | Meaning |
| --- | --- |
| `-s`, `--device SERIAL` | Which device. Only needed when several are attached. |
| `-b`, `--broker HOST` | MQTT broker. Default `localhost`. |
| `-P`, `--port PORT` | Broker port. Default `1883` (`8883` is the usual TLS port). |
| `-t`, `--topic TOPIC` | Topic to publish on; `{serial}` and `{id}` are substituted. Default `gps/{serial}`. |
| `-i`, `--interval SECONDS` | How often the device is read. Default `0.3` (≈3 Hz). |
| `--provider {any,fused,gps,network,passive}` | Which provider to follow. Default `any`, preferring `fused`, then `gps`. |
| `--source {dumpsys,logcat}` | Where coordinates come from. See **Notes**. Default `dumpsys`. |
| `--qos {0,1,2}` | MQTT QoS. Default `0`. |
| `--retain` | Retain each message, so a late subscriber gets the last position at once. |
| `-u`, `--username USER` | MQTT username. Falls back to `$MQTT_USERNAME`. |
| `--password [PASS]` | MQTT password. Pass the flag with no value for a hidden prompt. Falls back to `$MQTT_PASSWORD`. |
| `--tls` | Connect over TLS. |
| `--client-id ID` | MQTT client id. Default `mocklocation-PID`. |
| `--id NAME` | Publish as this name instead of the adb serial. |
| `--status-topic TOPIC` | Retained `online`/`offline`, with a matching last will. |
| `--dry-run` | Print the JSON instead of connecting to a broker. |
| `--from-file PATH` | Parse captured `dumpsys` text instead of a device. |
| `--list-devices` | List attached devices and exit. |
| `-v`, `--verbose` | Log every message and broker event to stderr. |
| `-h`, `--help` | Usage. |

## Authentication

If your broker requires a login (Mosquitto with `allow_anonymous false` and a
`password_file`, say), there are three ways to give it one. A flag wins over the
environment, and the environment wins over nothing:

```sh
./mocklocation.py -u testapp --password s3cret       # visible in ps and shell history
./mocklocation.py -u testapp --password              # hidden prompt
MQTT_USERNAME=testapp MQTT_PASSWORD=s3cret ./mocklocation.py   # nothing on the command line
```

```
$ ./mocklocation.py -u testapp --password
Password for testapp:
```

A wrong login is reported and exits 1 rather than being swallowed — the bridge waits
for the broker's CONNACK before it starts streaming, so it can't sit there publishing
into a connection the broker already refused:

```
error: broker rejected the connection: Bad user name or password
       check --username/--password (or MQTT_USERNAME/MQTT_PASSWORD)
```

MQTT has no way to send a password without a username, so `--password` on its own is
rejected. If the broker drops the connection later, paho reconnects underneath and the
bridge warns rather than going quiet.

## Output

One JSON object per new fix:

```json
{"lat": -1.286389, "lon": 36.817223, "alt": 1661.0, "accuracy": 5.0,
 "speed": 12.4, "bearing": 87.3, "provider": "fused",
 "device": "FAKE123", "ts": 1789564800.123, "time": "2026-09-17T09:20:00Z"}
```

`lat` and `lon` are always present. `speed` (m/s) and `bearing` (degrees) come from the
provider when it supplies them, and are otherwise derived from the previous fix — so
they are absent on the first message, and while the device sits still. `alt`, `accuracy`
and `provider` are passed through when the device reports them. `ts` is epoch seconds,
`time` the same moment as ISO-8601 UTC.

A message is published when the *position* changes, not on every poll: a parked device
publishes once and then goes quiet.

## Interactive mode

```
$ ./mocklocation.py -t fleet/car1/position -v
FAKE123 -> localhost:1883 fleet/car1/position
connected to broker (Success)
fleet/car1/position {"lat": -1.286299, "lon": 36.817343, ..., "provider": "fused"}
fleet/car1/position {"lat": -1.286209, "lon": 36.817463, "speed": 54.68, "bearing": 53.1, ...}
^C
published 2 fixes

aborted
```

Watch it from the other side with `mosquitto_sub -h localhost -t 'fleet/#' -v`.

## Notes and caveats

- **`--source dumpsys`** (the default) reads the device's last known fix. It works with
  any mock-location app, needs no root, and doesn't care whether the app logs anything.
  Rather than shelling out per poll, one long-lived `adb shell` loop runs on the phone
  and streams fixes back, which is what keeps it real time.
- **`--source logcat`** tails `adb logcat` and pulls coordinates out of log lines. It is
  push-based and instant, but only produces anything if the spoofing app — or your own
  app — actually logs coordinates. Try `--dry-run` with it before relying on it.
- Speed and bearing are derived using the device's own elapsed-time clock (`et=` in
  `dumpsys`), not the host's, so two fixes arriving in the same millisecond can't
  produce a warp-speed reading.
- Timestamps are host time, not device time. If the phone's clock is skewed, `ts`
  still reflects when the bridge saw the fix.
- Nothing is encrypted unless you pass `--tls`: with a plain connection the credentials
  and every coordinate cross the network in the clear. Fine on a test LAN, not beyond it.
- A password given as `--password s3cret` is visible in your shell history and in `ps`
  output. Use the bare `--password` prompt or `MQTT_PASSWORD` instead.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | No usable device, adb missing, broker unreachable, or Ctrl-C. |
| 2 | Bad usage (argparse). |

## Troubleshooting

**`error: no device attached; enable USB debugging and check adb devices`** — the phone
isn't visible to adb. Replug, and check `adb devices` yourself.

**`error: device X is unauthorized`** — accept the *Allow USB debugging?* prompt on the
phone, then rerun.

**`error: several devices attached (...); pick one with --device`** — pass `-s SERIAL`.

**`warning: the device reports no location at all`** — Android is holding no fix. Usually
Fake GPS isn't selected under Developer options → *Select mock location app*, or it
hasn't been started yet.

**`warning: no new fix for 10s -- is the route still running?`** — the position stopped
changing. Expected when the route ends or is paused; the bridge keeps waiting.

**`error: broker rejected the connection: Bad user name or password`** — the broker
refused the login. Check the credentials, and that the user exists in the broker's
password file.

**`error: broker rejected the connection: Not authorized`** — the login was accepted but
the broker won't let this client in; usually an ACL that doesn't cover the topic or
client id. Try `--client-id` and check the broker's ACL file.

**`error: no response from host:1883 after 10s -- is that an MQTT broker?`** — something
is listening on that port but never sent a CONNACK. Usually the wrong port, or a TLS
listener being spoken to in plaintext (add `--tls`).

**`error: a --password needs a --username`** — MQTT can't send a password on its own.
Add `-u USER` or set `MQTT_USERNAME`.

**`error: could not connect to host:1883 -- Connection refused`** — no broker there.
Start one (`brew install mosquitto && mosquitto -p 1883`) or point `-b`/`-P` elsewhere.

**Coordinates arrive but the app under test doesn't move** — check the topic matches
what the app subscribes to (`-v` prints the topic used), and subscribe with
`mosquitto_sub` yourself to see which side is at fault.
