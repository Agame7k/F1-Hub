"""Real-time OpenF1 data over MQTT (paid accounts): mqtt.openf1.org:8883, token as the password.

A tiny dependency-free MQTT 3.1.1 subscriber (QoS 0). Messages are buffered per topic and
drained by the LiveTracker each tick, so data arrives within a second or two instead of
waiting for the next REST poll - and none of it counts towards the 60 requests/minute limit.
"""
import gzip
import json
import os
import socket
import ssl
import struct
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from . import api

HOST, PORT = "mqtt.openf1.org", 8883
KEEPALIVE = 30
TOPICS = ("position", "intervals", "race_control", "laps", "location", "car_data",
          "weather", "overtakes", "team_radio", "pit", "stints")
MAX_BUFFER = 20000          # per topic, so a stalled consumer can't eat all the memory
RECORD_EVERY = 5.0          # seconds between flushes of the raw-message recording


def _varint(n):
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s):
    b = s.encode()
    return struct.pack("!H", len(b)) + b


class Stream:
    def __init__(self, topics=TOPICS, connect=True):
        self.topics = topics
        self.buf = defaultdict(list)
        self.lock = threading.Lock()
        self.connected = False
        self.last_msg = 0.0
        self.error = None
        self.received = 0
        self._stop = False
        self._sock = None
        self.on_message = None      # callback(topic) on the network thread - the feed uses it to rebuild at once
        self._lag = defaultdict(lambda: deque(maxlen=200))   # topic -> seconds from event (row "date") to arrival
        self._rec, self._rec_at = [], time.time()
        if connect:
            threading.Thread(target=self._run, daemon=True, name="openf1-mqtt").start()

    # ------------------------------------------------------------------ consumer side
    @property
    def healthy(self):
        return self.connected and time.time() - self.last_msg < 60

    def drain(self, topic, session_key=None):
        """All buffered rows for a topic (oldest first), optionally only for one session."""
        with self.lock:
            rows, self.buf[topic] = self.buf[topic], []
        if session_key is not None:
            rows = [r for r in rows if r.get("session_key") in (None, session_key)]
        return sorted(rows, key=lambda r: r.get("_id", 0))

    def lag(self):
        """Median seconds between something happening on track and its message reaching us, per topic."""
        out = {}
        for topic, d in list(self._lag.items()):
            v = sorted(d)
            if v:
                out[topic] = round(v[len(v) // 2], 2)
        return out

    def status(self):
        return {"connected": self.connected, "healthy": self.healthy, "received": self.received,
                "last_msg_age": round(time.time() - self.last_msg, 1) if self.last_msg else None,
                "error": self.error, "lag": self.lag()}

    def stop(self):
        self._stop = True
        self._flush_record()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ network side
    def _run(self):
        backoff = 2
        while not self._stop:
            try:
                self._session()
                backoff = 2
            except Exception as e:      # network drop, expired token, ... -> reconnect
                self.error = f"{type(e).__name__}: {e}"
            self.connected = False
            if self._stop:
                return
            time.sleep(backoff)
            backoff = min(60, backoff * 2)

    def _session(self):
        token = api._auth_token()
        if not token:
            raise RuntimeError("no OpenF1 login - streaming needs a paid account")
        raw = socket.create_connection((HOST, PORT), timeout=15)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)
        self._sock = sock
        user = os.environ.get("OPENF1_USERNAME") or "f1hub"
        # CONNECT: clean session, username + password(token)
        var = _str("MQTT") + bytes([4, 0xC2]) + struct.pack("!H", KEEPALIVE)
        payload = _str(f"f1hub-{int(time.time() * 1000) % 10**9}") + _str(user) + _str(token)
        sock.sendall(b"\x10" + _varint(len(var) + len(payload)) + var + payload)
        ptype, body = self._read(sock)
        if ptype != 2 or len(body) < 2 or body[1] != 0:
            raise RuntimeError(f"MQTT connect refused (code {body[1] if len(body) > 1 else '?'})")
        subs = b"".join(_str(f"v1/{t}") + b"\x00" for t in self.topics)
        sock.sendall(b"\x82" + _varint(2 + len(subs)) + struct.pack("!H", 1) + subs)
        self.connected, self.error = True, None
        self.last_msg = time.time()
        sock.settimeout(KEEPALIVE / 2)
        last_ping = time.time()
        while not self._stop:
            if time.time() - last_ping > KEEPALIVE / 2:
                sock.sendall(b"\xc0\x00")
                last_ping = time.time()
            if time.time() - self._rec_at > RECORD_EVERY:
                self._flush_record()
            try:
                ptype, body = self._read(sock)
            except socket.timeout:
                continue
            if ptype == 3:
                self._publish(body)

    # ------------------------------------------------------------------ recording
    def _flush_record(self):
        """Raw messages with their arrival time, next to the REST recordings (api.RECORD_DIR), so a session's
        real stream latency and message order can be studied afterwards. Never breaks live timing."""
        rows, self._rec, self._rec_at = self._rec, [], time.time()
        if not rows or api.RECORD_DIR is None:
            return
        try:
            folder = api.RECORD_DIR / time.strftime("%Y-%m-%d", time.gmtime())
            folder.mkdir(parents=True, exist_ok=True)
            with gzip.open(folder / "stream.jsonl.gz", "at", encoding="utf-8") as f:
                f.write("".join(json.dumps({"rx": rx, "topic": t, "row": r}) + "\n" for rx, t, r in rows))
        except OSError:
            pass

    def _publish(self, body):
        n = struct.unpack("!H", body[:2])[0]
        topic = body[2:2 + n].decode()
        try:
            row = json.loads(body[2 + n:])
        except ValueError:
            return
        name = topic.split("/", 1)[-1]
        rx = time.time()
        if api.RECORD_DIR is not None and isinstance(row, dict):
            self._rec.append((rx, name, dict(row)))
        if not isinstance(row, dict):
            return
        for k in ("date", "date_start"):     # some topics omit the UTC offset that REST includes
            v = row.get(k)
            if isinstance(v, str) and len(v) > 19 and not (v.endswith("Z") or "+" in v[19:] or "-" in v[19:]):
                row[k] = v + "+00:00"
            elif isinstance(v, str) and v.endswith("Z"):
                row[k] = v[:-1] + "+00:00"
        with self.lock:
            b = self.buf[name]
            b.append(row)
            if len(b) > MAX_BUFFER:
                del b[:len(b) - MAX_BUFFER]
        self.received += 1
        self.last_msg = rx
        v = row.get("date")
        if isinstance(v, str):
            try:
                self._lag[name].append(rx - datetime.fromisoformat(v).timestamp())
            except ValueError:
                pass
        cb = self.on_message
        if cb:
            try:
                cb(name)
            except Exception:
                pass

    @staticmethod
    def _recv(sock, n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("stream closed")
            data += chunk
        return data

    def _read(self, sock):
        head = self._recv(sock, 1)[0]
        mult, length = 1, 0
        while True:
            b = self._recv(sock, 1)[0]
            length += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        return head >> 4, self._recv(sock, length) if length else b""
