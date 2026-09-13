#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netguard - Datenverbrauchs-Waechter fuer macOS
==============================================

Was das Tool macht:
  * misst laufend den echten Traffic des Netzwerk-Interfaces (netstat -ibn)
  * ordnet den Traffic Prozessen zu (nettop -P), inkl. PID, User, Elternprozess
  * erkennt Bursts (z.B. "mehr als 50 MB in 60 Sekunden")
  * loggt alles nach JSONL auf die Platte (Samples + Incidents)
  * reagiert: nur melden / Prozess anhalten (SIGSTOP) / Interface runter /
    WLAN aus / pf-Firewall-Kill-Switch

Nur Python-Standardbibliothek. Getestet gedacht fuer macOS 13+.
Fuer volle Sichtbarkeit aller Prozesse als root laufen lassen (sudo).

Beispiele:
    sudo ./netguard.py top                  # wer zieht JETZT Daten?
    sudo ./netguard.py monitor --burst-mb 50 --window 60 --action notify
    sudo ./netguard.py monitor --burst-mb 50 --action suspend --daily-mb 1500
    sudo ./netguard.py unblock
    sudo ./netguard.py report               # Incidents der letzten Tage
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone

VERSION = "1.0"

# Prozesse, die niemals per SIGSTOP angehalten werden (sonst haengt das System)
NEVER_SUSPEND = {
    "kernel_task", "launchd", "kernel", "WindowServer", "loginwindow",
    "configd", "mDNSResponder", "opendirectoryd", "securityd", "syslogd",
    "notifyd", "diskarbitrationd", "coreaudiod", "powerd", "hidd",
    "UserEventAgent", "distnoted", "Finder", "SystemUIServer", "netguard.py",
    "sshd", "Terminal", "iTerm2", "WindowManager",
}

# ---------------------------------------------------------------- Hilfsfunktionen


def run(cmd, timeout=15):
    """Kommando ausfuehren, stdout als Text zurueck. Fehler -> leerer String."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout or ""
    except Exception:
        return ""


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def human(n):
    """Bytes hübsch formatieren."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def default_interface():
    """Interface der Default-Route ermitteln (z.B. en0)."""
    out = run(["/sbin/route", "-n", "get", "default"])
    m = re.search(r"interface:\s*(\S+)", out)
    return m.group(1) if m else "en0"


def console_user():
    """Der aktuell grafisch eingeloggte User - fuer Notifications."""
    out = run(["/usr/bin/stat", "-f", "%Su", "/dev/console"]).strip()
    return out or os.environ.get("SUDO_USER") or os.environ.get("USER") or ""


SOUND_DIR = "/System/Library/Sounds"


def _as_console_user(cmd):
    """
    Kommando in der GUI-Session des eingeloggten Users starten.
    Noetig, weil root via launchd keinen Zugriff auf Audio/Notifications hat.
    """
    user = console_user()
    if os.geteuid() == 0 and user and user != "root":
        uid = run(["/usr/bin/id", "-u", user]).strip()
        if uid.isdigit():
            return ["/bin/launchctl", "asuser", uid, "/usr/bin/sudo", "-u", user] + cmd
    return cmd


def play_alert(sound="Sosumi", repeat=3, volume=1.5, say_text=None):
    """
    Hoerbarer Alarm ueber afplay - unabhaengig von Benachrichtigungs-
    Einstellungen und 'Nicht stoeren'. sound=None/'none' schaltet ihn ab.
    Laeuft im Hintergrund, damit das Monitoring nicht blockiert.
    """
    if sound and sound.lower() != "none":
        path = sound if os.path.isabs(sound) else f"{SOUND_DIR}/{sound}.aiff"
        if os.path.exists(path):
            inner = "; ".join([f'/usr/bin/afplay -v {volume} "{path}"'] * max(1, repeat))
            try:
                subprocess.Popen(_as_console_user(["/bin/sh", "-c", inner]),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
        else:
            print(f"[netguard] Sound nicht gefunden: {path}", file=sys.stderr)

    if say_text:
        try:
            subprocess.Popen(_as_console_user(["/usr/bin/say", say_text[:200]]),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def notify(title, message):
    """macOS-Notification. Funktioniert auch, wenn wir als root via launchd laufen."""
    msg = message.replace('"', "'")[:400]
    ttl = title.replace('"', "'")[:80]
    script = f'display notification "{msg}" with title "{ttl}" sound name "Basso"'
    user = console_user()
    if os.geteuid() == 0 and user and user != "root":
        uid = run(["/usr/bin/id", "-u", user]).strip()
        if uid.isdigit():
            subprocess.run(
                ["/bin/launchctl", "asuser", uid, "/usr/bin/sudo", "-u", user,
                 "/usr/bin/osascript", "-e", script],
                capture_output=True,
            )
            return
    subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True)


# ---------------------------------------------------------------- Messung


def iface_counters(ifname):
    """
    Kumulative Byte-Zaehler des Interfaces aus `netstat -ibn`.
    Das ist die *Wahrheit* ueber den tatsaechlichen Verbrauch.
    Rueckgabe: (ibytes, obytes) oder None.
    """
    out = run(["/usr/sbin/netstat", "-ibn"])
    header = None
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "Name":
            header = parts
            continue
        if header is None or parts[0] != ifname:
            continue
        # Nur die <Link#n>-Zeile enthaelt die Interface-Gesamtsumme
        if len(parts) < 3 or not parts[2].startswith("<Link"):
            continue
        try:
            ib = int(parts[header.index("Ibytes")])
            ob = int(parts[header.index("Obytes")])
            return ib, ob
        except (ValueError, IndexError):
            continue
    return None


def nettop_sample(nettop_type=None):
    """
    Kumulative Bytes pro Prozess via nettop.
    Rueckgabe: dict {(name, pid): (bytes_in, bytes_out)}
    """
    cmd = ["/usr/bin/nettop", "-P", "-L", "1", "-x", "-n",
           "-J", "bytes_in,bytes_out"]
    if nettop_type:
        cmd += ["-t", nettop_type]
    out = run(cmd, timeout=20)

    result = {}
    labels = None
    for raw in out.splitlines():
        parts = [p.strip() for p in raw.split(",")]
        if not parts or not parts[0]:
            continue
        if labels is None:
            # Header: "time,,bytes_in,bytes_out," -> Spaltennamen einsammeln.
            # Achtung: Header und Datenzeilen haben unterschiedlich viele
            # Leerfelder, deshalb NICHT ueber Index mappen, sondern ueber die
            # Reihenfolge der nicht-leeren Labels ab Spalte 1.
            if "bytes_in" in parts or "bytes_out" in parts:
                labels = [p for p in parts[1:] if p]
            continue
        # Zeitstempel-Zeile ueberspringen
        if re.match(r"^\d{2}:\d{2}:\d{2}", parts[0]):
            continue
        key = parts[0]
        if "." not in key:
            continue
        name, _, pid = key.rpartition(".")
        if not pid.isdigit():
            continue
        # Datenfelder der Reihe nach den Labels zuordnen
        values = parts[1:1 + len(labels)]
        row = {}
        for lab, val in zip(labels, values):
            try:
                row[lab] = int(val or 0)
            except ValueError:
                row[lab] = 0
        if "bytes_in" not in row and "bytes_out" not in row:
            continue
        result[(name, int(pid))] = (row.get("bytes_in", 0), row.get("bytes_out", 0))
    return result


def diff_procs(prev, cur):
    """
    Delta pro Prozess zwischen zwei nettop-Samples.
    Zaehler-Reset (Prozess neu gestartet) wird als voller Wert gewertet.
    """
    deltas = {}
    for key, (bi, bo) in cur.items():
        pbi, pbo = prev.get(key, (0, 0))
        d_in = bi - pbi if bi >= pbi else bi
        d_out = bo - pbo if bo >= pbo else bo
        if d_in or d_out:
            deltas[key] = (d_in, d_out)
    return deltas


# ---------------------------------------------------------------- Attribution


def proc_info(pid):
    """Wer ist das, wem gehoert es, wer hat es gestartet?"""
    info = {"pid": pid, "user": "?", "ppid": None, "comm": "?",
            "args": "", "started": "?", "parents": []}
    line = run(["/bin/ps", "-p", str(pid), "-o", "user=,ppid=,etime=,comm="]).strip()
    if line:
        f = line.split(None, 3)
        if len(f) >= 4:
            info["user"], ppid, info["started"], info["comm"] = f[0], f[1], f[2], f[3]
            info["ppid"] = int(ppid) if ppid.isdigit() else None
    info["args"] = run(["/bin/ps", "-p", str(pid), "-o", "args="]).strip()[:500]

    # Elternkette hochlaufen - damit man sieht, WER das ausgeloest hat
    seen, cur = set(), info["ppid"]
    while cur and cur not in seen and cur > 1 and len(info["parents"]) < 6:
        seen.add(cur)
        pl = run(["/bin/ps", "-p", str(cur), "-o", "ppid=,user=,comm="]).strip()
        if not pl:
            break
        pf = pl.split(None, 2)
        if len(pf) < 3:
            break
        info["parents"].append({"pid": cur, "user": pf[1], "comm": pf[2]})
        cur = int(pf[0]) if pf[0].isdigit() else None
    return info


def proc_connections(pid, limit=12):
    """Offene Verbindungen des Prozesses - zeigt WOHIN der Traffic ging."""
    out = run(["/usr/sbin/lsof", "-nP", "-i", "-a", "-p", str(pid)], timeout=10)
    conns = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        name = parts[8]
        if "->" in name:
            conns.append(name.split("->")[-1])
        elif ":" in name:
            conns.append(name)
        if len(conns) >= limit:
            break
    # Duplikate raus, Reihenfolge behalten
    seen, uniq = set(), []
    for c in conns:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


# ---------------------------------------------------------------- Logging


class Logger:
    def __init__(self, logdir):
        self.dir = logdir
        os.makedirs(self.dir, exist_ok=True)
        self.samples = os.path.join(self.dir, "samples.jsonl")
        self.incidents = os.path.join(self.dir, "incidents.jsonl")
        self.state_file = os.path.join(self.dir, "state.json")

    def _append(self, path, obj):
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())   # damit nach einem Hard-Cut nichts fehlt
        except OSError as e:
            print(f"[netguard] Log-Fehler {path}: {e}", file=sys.stderr)

    def sample(self, obj):
        self._append(self.samples, obj)

    def incident(self, obj):
        self._append(self.incidents, obj)

    def read_state(self):
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def write_state(self, obj):
        try:
            with open(self.state_file, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def rotate(self, max_bytes=50 * 1024 * 1024):
        for path in (self.samples, self.incidents):
            try:
                if os.path.exists(path) and os.path.getsize(path) > max_bytes:
                    os.replace(path, path + ".1")
            except OSError:
                pass


# ---------------------------------------------------------------- Aktionen (Blocken)

PF_ANCHOR_FILE = "/etc/pf.anchors/netguard"


def act_suspend(procs, logger):
    """Top-Verursacher mit SIGSTOP einfrieren (reversibel via SIGCONT)."""
    stopped = []
    for name, pid, _ in procs[:3]:
        if name in NEVER_SUSPEND:
            continue
        try:
            os.kill(pid, signal.SIGSTOP)
            stopped.append({"name": name, "pid": pid})
        except (ProcessLookupError, PermissionError) as e:
            print(f"[netguard] konnte {name}.{pid} nicht anhalten: {e}", file=sys.stderr)
    if stopped:
        st = logger.read_state()
        st.setdefault("suspended", []).extend(stopped)
        st["blocked_at"] = now_iso()
        logger.write_state(st)
    return stopped


def act_iface_down(ifname, logger):
    subprocess.run(["/sbin/ifconfig", ifname, "down"], capture_output=True)
    st = logger.read_state()
    st["iface_down"] = ifname
    st["blocked_at"] = now_iso()
    logger.write_state(st)
    return [{"iface": ifname}]


def act_wifi_off(ifname, logger):
    subprocess.run(["/usr/sbin/networksetup", "-setairportpower", ifname, "off"],
                   capture_output=True)
    st = logger.read_state()
    st["wifi_off"] = ifname
    st["blocked_at"] = now_iso()
    logger.write_state(st)
    return [{"wifi": ifname}]


def act_pf_block(ifname, logger):
    """
    Kill-Switch ueber die pf-Firewall.
    Voraussetzung: einmalig `sudo netguard.py install-pf` ausgefuehrt.
    """
    rules = (
        f"block drop out quick on {ifname} all\n"
        f"block drop in quick on {ifname} all\n"
    )
    try:
        with open(PF_ANCHOR_FILE, "w", encoding="utf-8") as fh:
            fh.write(rules)
    except OSError as e:
        print(f"[netguard] pf-Anchor schreiben fehlgeschlagen: {e}", file=sys.stderr)
        return []
    subprocess.run(["/sbin/pfctl", "-a", "netguard", "-f", PF_ANCHOR_FILE],
                   capture_output=True)
    subprocess.run(["/sbin/pfctl", "-E"], capture_output=True)
    st = logger.read_state()
    st["pf_blocked"] = ifname
    st["blocked_at"] = now_iso()
    logger.write_state(st)
    return [{"pf": ifname}]


def do_unblock(logger, verbose=True):
    """Alles wieder freigeben."""
    st = logger.read_state()
    done = []

    for p in st.get("suspended", []):
        try:
            os.kill(p["pid"], signal.SIGCONT)
            done.append(f"SIGCONT {p['name']}.{p['pid']}")
        except (ProcessLookupError, PermissionError):
            pass
    st.pop("suspended", None)

    if st.get("iface_down"):
        subprocess.run(["/sbin/ifconfig", st["iface_down"], "up"], capture_output=True)
        done.append(f"Interface {st['iface_down']} up")
        st.pop("iface_down")

    if st.get("wifi_off"):
        subprocess.run(["/usr/sbin/networksetup", "-setairportpower",
                        st["wifi_off"], "on"], capture_output=True)
        done.append(f"WLAN {st['wifi_off']} an")
        st.pop("wifi_off")

    if st.get("pf_blocked"):
        try:
            with open(PF_ANCHOR_FILE, "w", encoding="utf-8") as fh:
                fh.write("# leer - netguard entsperrt\n")
        except OSError:
            pass
        subprocess.run(["/sbin/pfctl", "-a", "netguard", "-F", "rules"],
                       capture_output=True)
        done.append("pf-Anchor geleert")
        st.pop("pf_blocked")

    st.pop("blocked_at", None)
    st["unblocked_at"] = now_iso()
    logger.write_state(st)
    if verbose:
        print("Entsperrt:" if done else "Nichts war gesperrt.")
        for d in done:
            print("  -", d)
    return done


# ---------------------------------------------------------------- Monitor


class Monitor:
    def __init__(self, args):
        self.a = args
        self.iface = args.iface or default_interface()
        self.log = Logger(args.logdir)
        self.window = deque()          # (ts, delta_bytes, {proc: bytes})
        self.prev_procs = None
        self.prev_iface = None
        self.day = datetime.now().strftime("%Y-%m-%d")
        self.day_bytes = 0
        self.tripped = False
        self.trip_time = 0.0

    # -- Fenster-Buchhaltung -------------------------------------------------

    def push(self, ts, total_delta, proc_deltas):
        self.window.append((ts, total_delta, proc_deltas))
        cutoff = ts - self.a.window
        while self.window and self.window[0][0] < cutoff:
            self.window.popleft()

    def window_total(self):
        return sum(w[1] for w in self.window)

    def window_top(self, n=8):
        """Top-Verursacher im aktuellen Fenster: [(name, pid, bytes), ...]"""
        agg = {}
        for _, _, procs in self.window:
            for (name, pid), b in procs.items():
                agg[(name, pid)] = agg.get((name, pid), 0) + b
        ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
        return [(k[0], k[1], v) for k, v in ranked[:n]]

    # -- Incident ------------------------------------------------------------

    def build_incident(self, reason, amount):
        top = self.window_top()
        culprits = []
        for name, pid, byts in top:
            if byts <= 0:
                continue
            entry = {"process": name, "pid": pid, "bytes": byts,
                     "human": human(byts)}
            entry.update(proc_info(pid))
            entry["connections"] = proc_connections(pid)
            culprits.append(entry)
        return {
            "ts": now_iso(),
            "type": "incident",
            "reason": reason,
            "interface": self.iface,
            "window_seconds": self.a.window,
            "bytes": amount,
            "human": human(amount),
            "day_total_bytes": self.day_bytes,
            "day_total_human": human(self.day_bytes),
            "action": self.a.action,
            "culprits": culprits,
        }

    def print_incident(self, inc):
        print("\n" + "=" * 72)
        print(f"!! {inc['reason']} um {inc['ts']}")
        print(f"   {inc['human']} in {inc['window_seconds']}s auf {inc['interface']}")
        print(f"   heute gesamt: {inc['day_total_human']}")
        print("-" * 72)
        for c in inc["culprits"][:5]:
            print(f"   {c['human']:>10}  {c['process']}.{c['pid']}"
                  f"  user={c['user']}  uptime={c['started']}")
            if c["parents"]:
                chain = " < ".join(f"{p['comm']}({p['pid']})" for p in c["parents"])
                print(f"               gestartet von: {chain}")
            if c["args"]:
                print(f"               cmd: {c['args'][:110]}")
            if c["connections"]:
                print(f"               -> {', '.join(c['connections'][:5])}")
        print("=" * 72 + "\n")

    def apply_action(self, inc):
        top = self.window_top()
        act = self.a.action
        if act == "notify":
            result = []
        elif act == "suspend":
            result = act_suspend(top, self.log)
        elif act == "iface":
            result = act_iface_down(self.iface, self.log)
        elif act == "wifi":
            result = act_wifi_off(self.iface, self.log)
        elif act == "pf":
            result = act_pf_block(self.iface, self.log)
        else:
            result = []
        inc["action_result"] = result
        names = ", ".join(f"{c['process']}" for c in inc["culprits"][:3]) or "unbekannt"

        # Erst der Ton - der soll auch dann kommen, wenn osascript haengt
        play_alert(sound=self.a.sound,
                   repeat=self.a.sound_repeat,
                   volume=self.a.sound_volume,
                   say_text=(self.a.say or None))

        if not self.a.no_notify:
            notify("netguard: Traffic-Burst",
                   f"{inc['human']} in {inc['window_seconds']}s - {names} "
                   f"[{act}]")
        return result

    # -- Hauptschleife -------------------------------------------------------

    def loop(self):
        print(f"netguard {VERSION} - Interface {self.iface}, "
              f"Intervall {self.a.interval}s, Fenster {self.a.window}s")
        print(f"Schwelle: {self.a.burst_mb} MB / {self.a.window}s"
              + (f", Tageslimit {self.a.daily_mb} MB" if self.a.daily_mb else "")
              + f", Aktion: {self.a.action}")
        print(f"Logs: {self.log.dir}")
        if os.geteuid() != 0:
            print("Hinweis: ohne sudo siehst du nur deine eigenen Prozesse.")
        print("Strg-C zum Beenden.\n")

        burst_limit = int(self.a.burst_mb * 1024 * 1024)
        daily_limit = int(self.a.daily_mb * 1024 * 1024) if self.a.daily_mb else 0

        while True:
            ts = time.time()

            # Tageswechsel
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self.day:
                self.day, self.day_bytes = today, 0
                self.tripped = False

            cur_iface = iface_counters(self.iface)
            cur_procs = nettop_sample(self.a.nettop_type)

            total_delta = 0
            if cur_iface and self.prev_iface:
                d_in = cur_iface[0] - self.prev_iface[0]
                d_out = cur_iface[1] - self.prev_iface[1]
                # negativ = Interface-Reset (down/up) -> ignorieren
                total_delta = max(0, d_in) + max(0, d_out)
            self.prev_iface = cur_iface or self.prev_iface

            proc_deltas = {}
            if self.prev_procs is not None:
                for key, (di, do) in diff_procs(self.prev_procs, cur_procs).items():
                    proc_deltas[key] = di + do
            self.prev_procs = cur_procs

            self.day_bytes += total_delta
            self.push(ts, total_delta, proc_deltas)
            win = self.window_total()

            # Sample-Log (nur oberhalb eines Rauschbodens, sonst wird die Datei riesig)
            if total_delta >= self.a.log_floor_kb * 1024:
                top3 = sorted(proc_deltas.items(), key=lambda kv: kv[1], reverse=True)[:5]
                self.log.sample({
                    "ts": now_iso(), "type": "sample", "iface": self.iface,
                    "bytes": total_delta, "window_bytes": win,
                    "day_bytes": self.day_bytes,
                    "top": [{"process": k[0], "pid": k[1], "bytes": v} for k, v in top3],
                })

            if self.a.verbose:
                top1 = max(proc_deltas.items(), key=lambda kv: kv[1], default=None)
                tag = f"{top1[0][0]}.{top1[0][1]}" if top1 else "-"
                print(f"{datetime.now():%H:%M:%S}  +{human(total_delta):>9}  "
                      f"Fenster {human(win):>9}  heute {human(self.day_bytes):>9}  "
                      f"top: {tag}")

            # --- Auslöser pruefen
            reason = None
            amount = win
            if win >= burst_limit:
                reason = f"Burst: {human(win)} in {self.a.window}s (Limit {self.a.burst_mb} MB)"
            elif daily_limit and self.day_bytes >= daily_limit:
                reason = f"Tageslimit erreicht: {human(self.day_bytes)}"
                amount = self.day_bytes

            if reason and not self.tripped:
                inc = self.build_incident(reason, amount)
                self.apply_action(inc)
                self.log.incident(inc)
                self.print_incident(inc)
                self.tripped = True
                self.trip_time = ts
                if self.a.action in ("iface", "wifi", "pf"):
                    print("Netz ist gesperrt. Freigeben mit:  sudo netguard.py unblock")
                    if self.a.stop_after_trip:
                        return
            elif self.tripped and ts - self.trip_time > self.a.cooldown:
                # Nach Cooldown wieder scharf, wenn es sich beruhigt hat
                if win < burst_limit and not (daily_limit and self.day_bytes >= daily_limit):
                    self.tripped = False
                    print(f"[{datetime.now():%H:%M:%S}] wieder scharf.")

            self.log.rotate()
            time.sleep(max(1, self.a.interval))


# ---------------------------------------------------------------- Subcommands


def cmd_top(args):
    """Einmal-Messung: wer zieht gerade Daten?"""
    iface = args.iface or default_interface()
    dur = args.duration
    print(f"Messe {dur}s auf {iface} ...")
    p0 = nettop_sample(args.nettop_type)
    i0 = iface_counters(iface)
    time.sleep(dur)
    p1 = nettop_sample(args.nettop_type)
    i1 = iface_counters(iface)

    deltas = diff_procs(p0, p1)
    ranked = sorted(((k, v[0] + v[1]) for k, v in deltas.items()),
                    key=lambda kv: kv[1], reverse=True)

    if i0 and i1:
        tot = max(0, i1[0] - i0[0]) + max(0, i1[1] - i0[1])
        print(f"\nInterface {iface} gesamt: {human(tot)}  "
              f"({human(tot / dur)}/s)\n")

    print(f"{'Bytes':>11}  {'Rate/s':>10}  Prozess")
    print("-" * 72)
    for (name, pid), b in ranked[:args.limit]:
        if b <= 0:
            continue
        info = proc_info(pid)
        print(f"{human(b):>11}  {human(b / dur):>10}  {name}.{pid}  "
              f"user={info['user']}")
        if args.verbose:
            if info["parents"]:
                chain = " < ".join(f"{p['comm']}({p['pid']})" for p in info["parents"])
                print(f"{'':>25}gestartet von: {chain}")
            if info["args"]:
                print(f"{'':>25}cmd: {info['args'][:100]}")
            conns = proc_connections(pid, 4)
            if conns:
                print(f"{'':>25}-> {', '.join(conns)}")
    return 0


def cmd_status(args):
    log = Logger(args.logdir)
    st = log.read_state()
    iface = args.iface or default_interface()
    print(f"Interface:  {iface}")
    c = iface_counters(iface)
    if c:
        print(f"Zaehler:    in {human(c[0])} / out {human(c[1])} (seit Boot)")
    if st.get("blocked_at"):
        print(f"GESPERRT seit {st['blocked_at']}")
        for k in ("iface_down", "wifi_off", "pf_blocked"):
            if st.get(k):
                print(f"  {k}: {st[k]}")
        for p in st.get("suspended", []):
            print(f"  angehalten: {p['name']}.{p['pid']}")
        print("Freigeben:  sudo netguard.py unblock")
    else:
        print("Status:     nicht gesperrt")
    print(f"Logs:       {log.dir}")
    return 0


def cmd_unblock(args):
    do_unblock(Logger(args.logdir))
    return 0


def cmd_report(args):
    """Incidents aus dem Log zusammenfassen."""
    log = Logger(args.logdir)
    if not os.path.exists(log.incidents):
        print("Keine Incidents protokolliert.")
        return 0
    rows = []
    with open(log.incidents, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    rows = rows[-args.limit:]
    for inc in rows:
        print(f"\n{inc['ts']}  {inc['human']} in {inc.get('window_seconds')}s"
              f"  [{inc.get('action')}]")
        print(f"  {inc['reason']}")
        for c in inc.get("culprits", [])[:4]:
            print(f"    {c['human']:>10}  {c['process']}.{c['pid']}  user={c['user']}")
            if c.get("connections"):
                print(f"                -> {', '.join(c['connections'][:4])}")
    # Aggregat: welcher Prozess taucht am haeufigsten auf?
    tally = {}
    for inc in rows:
        for c in inc.get("culprits", [])[:3]:
            tally[c["process"]] = tally.get(c["process"], 0) + c["bytes"]
    if tally:
        print("\nSumme pro Prozess ueber alle Incidents:")
        for name, b in sorted(tally.items(), key=lambda kv: kv[1], reverse=True)[:10]:
            print(f"  {human(b):>11}  {name}")
    return 0


def cmd_testsound(args):
    """Alarm einmal ausloesen, damit man ihn vorher hoert und einstellen kann."""
    avail = sorted(f[:-5] for f in os.listdir(SOUND_DIR)
                   if f.endswith(".aiff")) if os.path.isdir(SOUND_DIR) else []
    if avail:
        print("Verfuegbare Systemsounds: " + ", ".join(avail))
    print(f"Spiele '{args.sound}' {args.sound_repeat}x bei Lautstaerke "
          f"{args.sound_volume} ...")
    play_alert(args.sound, args.sound_repeat, args.sound_volume,
               args.say or None)
    notify("netguard", "Testalarm - so sieht der Vorfall aus.")
    time.sleep(4)
    return 0


def cmd_install_pf(args):
    """Einmalige Vorbereitung, damit der pf-Kill-Switch greift."""
    if os.geteuid() != 0:
        print("Bitte mit sudo ausfuehren.")
        return 1
    os.makedirs("/etc/pf.anchors", exist_ok=True)
    if not os.path.exists(PF_ANCHOR_FILE):
        with open(PF_ANCHOR_FILE, "w", encoding="utf-8") as fh:
            fh.write("# leer - wird von netguard befuellt\n")
    conf = "/etc/pf.conf"
    with open(conf, encoding="utf-8") as fh:
        text = fh.read()
    if 'anchor "netguard"' in text:
        print("/etc/pf.conf ist bereits vorbereitet.")
        return 0
    addition = ('anchor "netguard"\n'
                f'load anchor "netguard" from "{PF_ANCHOR_FILE}"\n')
    print("Folgendes wird an /etc/pf.conf angehaengt:\n")
    print(addition)
    if not args.yes:
        print("Nochmal mit --yes ausfuehren, um es wirklich zu schreiben.")
        return 0
    backup = conf + ".netguard.bak"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Backup: {backup}")
    with open(conf, "a", encoding="utf-8") as fh:
        fh.write("\n" + addition)
    subprocess.run(["/sbin/pfctl", "-f", conf], capture_output=True)
    print("Fertig. pf-Kill-Switch nutzbar mit --action pf")
    return 0


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.netguard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>{script}</string>
    <string>monitor</string>
    <string>--burst-mb</string><string>{burst}</string>
    <string>--window</string><string>{window}</string>
    <string>--action</string><string>{action}</string>
    <string>--sound</string><string>{sound}</string>
    <string>--sound-repeat</string><string>{sound_repeat}</string>
    <string>--logdir</string><string>{logdir}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logdir}/netguard.out.log</string>
  <key>StandardErrorPath</key><string>{logdir}/netguard.err.log</string>
</dict>
</plist>
"""


def cmd_install_agent(args):
    script = os.path.abspath(__file__)
    plist = PLIST.format(script=script, burst=args.burst_mb, window=args.window,
                         action=args.action, logdir=args.logdir,
                         sound=args.sound, sound_repeat=args.sound_repeat)
    target = "/Library/LaunchDaemons/local.netguard.plist"
    if not args.yes:
        print(f"Wuerde schreiben nach {target}:\n")
        print(plist)
        print("Nochmal mit --yes zum Installieren.")
        return 0
    if os.geteuid() != 0:
        print("Bitte mit sudo ausfuehren.")
        return 1
    os.makedirs(args.logdir, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(plist)
    os.chmod(target, 0o644)
    os.chown(target, 0, 0)
    subprocess.run(["/bin/launchctl", "bootout", "system", target], capture_output=True)
    subprocess.run(["/bin/launchctl", "bootstrap", "system", target], capture_output=True)
    print(f"Installiert und gestartet: {target}")
    print("Stoppen: sudo launchctl bootout system " + target)
    return 0


# ---------------------------------------------------------------- CLI


def build_parser():
    p = argparse.ArgumentParser(
        prog="netguard.py",
        description="Datenverbrauch ueberwachen, Verursacher ermitteln, Bursts blocken.")
    p.add_argument("--logdir", default=(
        "/var/log/netguard" if os.geteuid() == 0
        else os.path.expanduser("~/Library/Logs/netguard")))
    p.add_argument("--iface", default=None, help="z.B. en0 (Default: Default-Route)")
    p.add_argument("--nettop-type", default=None,
                   choices=["wifi", "wired", "loopback", "awdl"],
                   help="nettop auf einen Interface-Typ einschraenken")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("monitor", help="Dauerueberwachung")
    m.add_argument("--interval", type=int, default=5, help="Messintervall in s")
    m.add_argument("--window", type=int, default=60, help="Burst-Fenster in s")
    m.add_argument("--burst-mb", type=float, default=50.0,
                   help="MB im Fenster, ab denen ausgeloest wird")
    m.add_argument("--daily-mb", type=float, default=0.0,
                   help="optionales Tageslimit in MB (0 = aus)")
    m.add_argument("--action", default="notify",
                   choices=["notify", "suspend", "iface", "wifi", "pf"])
    m.add_argument("--cooldown", type=int, default=300,
                   help="Sekunden bis erneut ausgeloest werden kann")
    m.add_argument("--log-floor-kb", type=int, default=64,
                   help="Samples unter diesem Wert nicht loggen")
    m.add_argument("--no-notify", action="store_true")
    m.add_argument("--sound", default="Sosumi",
                   help="Systemsound aus /System/Library/Sounds oder Pfad "
                        "zu einer Audiodatei. 'none' schaltet den Ton ab.")
    m.add_argument("--sound-repeat", type=int, default=3,
                   help="wie oft der Ton hintereinander abgespielt wird")
    m.add_argument("--sound-volume", type=float, default=1.5,
                   help="Lautstaerke fuer afplay (1.0 = normal)")
    m.add_argument("--say", default="",
                   help="zusaetzliche Sprachansage, z.B. 'Achtung, Datenburst'")
    m.add_argument("--stop-after-trip", action="store_true",
                   help="nach hartem Block beenden")
    m.set_defaults(func=lambda a: Monitor(a).loop())

    ts = sub.add_parser("testsound", help="Alarmton einmal abspielen")
    ts.add_argument("--sound", default="Sosumi")
    ts.add_argument("--sound-repeat", type=int, default=3)
    ts.add_argument("--sound-volume", type=float, default=1.5)
    ts.add_argument("--say", default="")
    ts.set_defaults(func=cmd_testsound)

    t = sub.add_parser("top", help="Momentaufnahme: wer zieht gerade Daten?")
    t.add_argument("--duration", type=int, default=10)
    t.add_argument("--limit", type=int, default=15)
    t.set_defaults(func=cmd_top)

    s = sub.add_parser("status", help="Sperrstatus anzeigen")
    s.set_defaults(func=cmd_status)

    u = sub.add_parser("unblock", help="Sperre aufheben")
    u.set_defaults(func=cmd_unblock)

    r = sub.add_parser("report", help="Incidents zusammenfassen")
    r.add_argument("--limit", type=int, default=20)
    r.set_defaults(func=cmd_report)

    ip = sub.add_parser("install-pf", help="pf-Kill-Switch vorbereiten (einmalig)")
    ip.add_argument("--yes", action="store_true")
    ip.set_defaults(func=cmd_install_pf)

    ia = sub.add_parser("install-agent", help="als LaunchDaemon installieren")
    ia.add_argument("--yes", action="store_true")
    ia.add_argument("--burst-mb", type=float, default=50.0)
    ia.add_argument("--window", type=int, default=60)
    ia.add_argument("--action", default="notify",
                    choices=["notify", "suspend", "iface", "wifi", "pf"])
    ia.add_argument("--sound", default="Sosumi")
    ia.add_argument("--sound-repeat", type=int, default=3)
    ia.set_defaults(func=cmd_install_agent)

    return p


def main():
    args = build_parser().parse_args()
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0


if __name__ == "__main__":
    sys.exit(main())