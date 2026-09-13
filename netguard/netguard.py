#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netguard - Datenverbrauchs-Waechter fuer macOS
==============================================

Was das Tool macht:
  * misst laufend den echten Traffic des Netzwerk-Interfaces (netstat)
  * ordnet den Traffic Prozessen zu (nettop -P -d), inkl. PID, User, Elternprozess
  * erkennt Bursts (z.B. "mehr als 50 MB in 60 Sekunden")
  * loggt alles nach JSONL auf die Platte (Samples + Incidents)
  * reagiert: nur melden / Prozess anhalten (SIGSTOP) / Interface runter /
    WLAN aus / pf-Firewall-Kill-Switch

Nur Python-Standardbibliothek. Gedacht fuer macOS 13+.
Fuer volle Sichtbarkeit aller Prozesse als root laufen lassen (sudo).

Beispiele:
    sudo ./netguard.py top                  # wer zieht JETZT Daten?
    sudo ./netguard.py monitor --burst-mb 50 --window 60 --action notify
    sudo ./netguard.py monitor --burst-mb 50 --action suspend --daily-mb 1500
    sudo ./netguard.py unblock
    sudo ./netguard.py report               # Incidents der letzten Tage

Aenderungen 1.1:
  * nettop laeuft im Delta-Modus (-d -L 2 -s <interval>) und misst waehrend
    des gesamten Intervalls statt nur ~1s pro Zyklus. Keine Kumulativ-Diffs
    mehr, damit auch keine Fehlzaehlung beim Schliessen von Sockets.
  * Attribution wird gegen das netstat-Delta plausibilisiert; bei anhaltend
    unplausiblen Werten faellt netguard automatisch auf den alten
    Diff-Modus zurueck (--attrib erzwingt eine Variante).
  * netstat-Spalten werden relativ zur <Link#n>-Spalte gelesen (vorher
    falsche Werte bei Interfaces ohne MAC-Adresse, z.B. utun/VPN).
  * Fenster/Cooldown auf time.monotonic(); Standby- und Hang-Luecken
    werden erkannt und nicht als Burst gewertet.
  * Bei einem Incident wird zuerst geblockt und alarmiert, danach die
    (langsame) Forensik erhoben.
  * Notifications ueber osascript-Argumente statt String-Interpolation,
    mit Timeout - eine haengende GUI blockiert das Monitoring nicht mehr.
  * SIGSTOP trifft nie netguard selbst; PID-Wiederverwendung wird beim
    Entsperren ueber die Startzeit geprueft.
  * pf: Enable-Token wird gesichert und beim Entsperren zurueckgegeben,
    bestehende States werden geflusht (sonst laeuft der Download weiter).
  * SIGTERM/SIGINT geben angehaltene Prozesse wieder frei.
  * Tagesvolumen ueberlebt einen Neustart (state.json).
  * Globale Optionen funktionieren vor UND hinter dem Subkommando; der
    LaunchDaemon startete vorher wegen "--logdir" hinter "monitor" nie.

Aenderungen 1.2:
  * Mehrere Warnstufen statt eines einzigen Ausloesers: --stage MB:AKTION:...
    kann beliebig oft angegeben werden, netguard eskaliert von Stufe zu Stufe
    und faellt nach --cooldown wieder auf Stufe 0 zurueck.
  * Testmodus --simulate MB/s: taeuscht Verbrauch vor, spielt die echten
    Toene, fuehrt aber KEINE Aktion aus und schreibt in ein eigenes Logdir.
  * Aktion "kill" (SIGTERM, dann SIGKILL) zusaetzlich zu "suspend".
  * WLAN-Aktion ermittelt das WLAN-Geraet ueber networksetup, statt das
    Interface der Default-Route zu nehmen (das kann Ethernet sein).
"""

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

VERSION = "1.2"
VERBOSE = False
SIM_NAME = "simulierter-Download"

# Prozesse, die niemals per SIGSTOP angehalten werden (sonst haengt das System)
NEVER_SUSPEND = {
    # Einfrieren dieser Prozesse haengt Oberflaeche, Anmeldung oder das
    # Prozessmanagement des Systems auf.
    "kernel_task", "launchd", "kernel", "WindowServer", "loginwindow",
    "configd", "mDNSResponder", "opendirectoryd", "securityd", "syslogd",
    "notifyd", "diskarbitrationd", "coreaudiod", "powerd", "hidd",
    "UserEventAgent", "distnoted", "Finder", "SystemUIServer", "netguard.py",
    "sshd", "Terminal", "iTerm2", "WindowManager", "trustd", "apsd",
    "bluetoothd", "nsurlsessiond", "symptomsd", "networkd", "remoted",
    # Nachtrag aus dem Review: cfprefsd blockiert jede Einstellungsabfrage,
    # runningboardd verwaltet Prozesszustaende, fileproviderd haengt Finder
    # auf. cloudd und bird sind am Hotspot haeufige Vielverbraucher - sie
    # hier zu schuetzen heisst, dass Stufe 2 gegen eine ausser Kontrolle
    # geratene iCloud-Synchronisation nichts ausrichtet; dafuer ist Stufe 3
    # zustaendig. Siehe TASKS.md.
    "cfprefsd", "runningboardd", "fileproviderd", "softwareupdated",
    "Dock", "cloudd", "bird",
}

# Ab diesem Anteil am Fenstervolumen gilt ein Prozess als Verursacher.
MIN_SUSPEND_SHARE = 0.20


# Attribution gilt als unplausibel, wenn die Summe der Prozess-Bytes das
# netstat-Delta um diesen Faktor (plus Toleranz) uebersteigt.
IMPLAUSIBLE_FACTOR = 5
IMPLAUSIBLE_SLACK = 10 * 1024 * 1024
IMPLAUSIBLE_STREAK = 3

# ---------------------------------------------------------------- Hilfsfunktionen


def run(cmd, timeout=15):
    """
    Kommando ausfuehren, stdout als Text zurueck. Fehler -> leerer String.
    LC_ALL=C, weil hier Ausgaben geparst werden: `ps -o lstart=` etwa
    formatiert das Datum sonst nach Locale.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, LC_ALL="C", LANG="C"))
        if VERBOSE and p.returncode != 0:
            err = (p.stderr or "").strip().splitlines()
            print(f"[netguard] {cmd[0]} rc={p.returncode}: {err[0] if err else ''}",
                  file=sys.stderr)
        return p.stdout or ""
    except subprocess.TimeoutExpired:
        print(f"[netguard] Timeout: {cmd[0]}", file=sys.stderr)
        return ""
    except OSError as e:
        if VERBOSE:
            print(f"[netguard] {cmd[0]}: {e}", file=sys.stderr)
        return ""


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def human(n):
    """Bytes huebsch formatieren."""
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


def wifi_device():
    """
    Das WLAN-Geraet ermitteln statt es zu raten: networksetup erwartet den
    Geraetenamen, und die Default-Route kann auf Ethernet oder USB zeigen.
    """
    out = run(["/usr/sbin/networksetup", "-listallhardwareports"])
    m = re.search(r"Hardware Port:\s*(?:Wi-Fi|AirPort)\s*\nDevice:\s*(\S+)", out)
    return m.group(1) if m else None


def default_nettop_type(ifname):
    """
    nettop auf den Typ des gemessenen Interfaces einschraenken. Sonst zaehlt
    es auch Loopback- und AirDrop-Verkehr mit, den der netstat-Zaehler des
    Interfaces nie sieht - die beiden Summen passen dann nicht zusammen,
    obwohl jede fuer sich stimmt.
    """
    wifi = wifi_device()
    if wifi and ifname == wifi:
        return "wifi"
    if ifname.startswith(("en", "bridge")):
        return "wired"
    return None          # utun/VPN und Unbekanntes lieber nicht einschraenken


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
            one = f"/usr/bin/afplay -v {float(volume)} {shlex.quote(path)}"
            inner = "; ".join([one] * max(1, int(repeat)))
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


def notify(title, message, timeout=10):
    """
    macOS-Notification. Text wird als Argument uebergeben, nicht in das
    AppleScript interpoliert - Prozessnamen sind Fremdeingabe.
    Timeout, weil osascript ohne aktive GUI-Session haengen kann.
    """
    script = ("on run {msg, ttl}\n"
              "  display notification msg with title ttl sound name \"Basso\"\n"
              "end run")
    cmd = ["/usr/bin/osascript", "-e", script, message[:400], title[:80]]
    user = console_user()
    if os.geteuid() == 0 and user and user != "root":
        uid = run(["/usr/bin/id", "-u", user]).strip()
        if uid.isdigit():
            cmd = ["/bin/launchctl", "asuser", uid, "/usr/bin/sudo", "-u", user] + cmd
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[netguard] Notification fehlgeschlagen: {e}", file=sys.stderr)


# ---------------------------------------------------------------- Messung


def _parse_netstat_link(out, ifname):
    """
    Zaehler aus der <Link#n>-Zeile lesen - relativ zur Link-Spalte, nicht
    ueber den Header-Index: bei Interfaces ohne MAC-Adresse (utun, lo0)
    fehlt die Address-Spalte und alle Header-Indizes verschieben sich.
    Layout hinter <Link#n>: [Address] Ipkts Ierrs Ibytes Opkts Oerrs Obytes ...
    """
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != ifname:
            continue
        link = next((i for i, p in enumerate(parts) if p.startswith("<Link")), None)
        if link is None:
            continue
        tail = parts[link + 1:]
        if tail and not tail[0].isdigit():
            tail = tail[1:]                      # MAC-Adresse ueberspringen
        nums = []
        for t in tail:
            if not t.isdigit():
                break
            nums.append(int(t))
        if len(nums) >= 6:
            return nums[2], nums[5]              # Ibytes, Obytes
    return None


def iface_counters(ifname):
    """
    Kumulative Byte-Zaehler des Interfaces. Das ist die *Wahrheit* ueber den
    tatsaechlichen Verbrauch. Rueckgabe: (ibytes, obytes) oder None.
    """
    c = _parse_netstat_link(run(["/usr/sbin/netstat", "-bnI", ifname]), ifname)
    if c is None:
        c = _parse_netstat_link(run(["/usr/sbin/netstat", "-ibn"]), ifname)
    return c


def nettop_sample(seconds, nettop_type=None, delta=True):
    """
    Bytes pro Prozess ueber `seconds` Sekunden.

    delta=True:  nettop -d, zweites Sample = Verbrauch seit dem ersten.
                 nettop misst dabei durchgehend, nicht nur ~1s pro Zyklus.
    delta=False: kumulative Werte (fuer den Diff-Fallback).

    Rueckgabe: (dict {(name, pid): (bytes_in, bytes_out)}, ok)
    ok=False -> nettop hat kein zweites Sample geliefert, die Werte taugen
    nicht als Delta. Das Volumen aus netstat bleibt davon unberuehrt.
    """
    secs = max(1, int(seconds))
    cmd = ["/usr/bin/nettop", "-P", "-x", "-n", "-L", "2", "-s", str(secs),
           "-J", "bytes_in,bytes_out"]
    if delta:
        cmd.insert(2, "-d")
    if nettop_type:
        cmd += ["-t", nettop_type]
    out = run(cmd, timeout=secs * 3 + 20)

    samples, cur, labels = [], None, None
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if not parts:
            continue
        # Kopfzeile, z.B. ",bytes_in,bytes_out," - das erste Feld ist LEER,
        # deshalb darf hier nicht vorher auf "leeres erstes Feld" gefiltert
        # werden. Sie wiederholt sich pro Sample und ist auf aktuellen
        # macOS-Versionen die einzige Sample-Grenze.
        # Achtung: Kopf- und Datenzeilen haben unterschiedlich viele
        # Leerfelder, deshalb NICHT ueber Index mappen, sondern ueber die
        # Reihenfolge der nicht-leeren Labels.
        if "bytes_in" in parts or "bytes_out" in parts:
            labels = [p for p in parts[1:] if p]
            cur = {}
            samples.append(cur)
            continue
        if not parts[0] or labels is None:
            continue
        # Aeltere Versionen trennen die Samples zusaetzlich per Zeitstempel
        if re.match(r"^\d{1,2}:\d{2}:\d{2}", parts[0]):
            cur = {}
            samples.append(cur)
            continue
        key = parts[0]
        if "." not in key:
            continue
        name, _, pid = key.rpartition(".")
        if not pid.isdigit():
            continue
        row = {}
        for lab, val in zip(labels, parts[1:1 + len(labels)]):
            try:
                row[lab] = int(val or 0)
            except ValueError:
                row[lab] = 0
        if "bytes_in" not in row and "bytes_out" not in row:
            continue
        cur[(name, int(pid))] = (row.get("bytes_in", 0), row.get("bytes_out", 0))

    # abgeschnittener oder leerer letzter Block (Kopfzeile ohne Daten)
    while len(samples) > 1 and not samples[-1]:
        samples.pop()

    if not samples:
        return {}, False
    # Erstes Sample ist im Delta-Modus die Baseline und nie ein Delta.
    return samples[-1], (len(samples) >= 2 or not delta)


def diff_procs(prev, cur):
    """
    Delta pro Prozess zwischen zwei kumulativen Samples (Fallback-Modus).
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


def ps_snapshot():
    """
    Einmal alle Prozesse einlesen statt pro Verursacher 8 ps-Aufrufe.
    Rueckgabe: {pid: {ppid, user, started, comm, args}}
    """
    snap = {}
    for line in run(["/bin/ps", "-axo", "pid=,ppid=,user=,etime=,comm="]).splitlines():
        f = line.split(None, 4)
        if len(f) < 5 or not f[0].isdigit():
            continue
        snap[int(f[0])] = {
            "ppid": int(f[1]) if f[1].isdigit() else None,
            "user": f[2], "started": f[3], "comm": f[4], "args": "",
        }
    for line in run(["/bin/ps", "-axo", "pid=,args="]).splitlines():
        f = line.split(None, 1)
        if len(f) == 2 and f[0].isdigit():
            e = snap.get(int(f[0]))
            if e is not None:
                e["args"] = f[1][:500]
    return snap


def proc_info(pid, snap=None):
    """Wer ist das, wem gehoert es, wer hat es gestartet?"""
    info = {"pid": pid, "user": "?", "ppid": None, "comm": "?",
            "args": "", "started": "?", "parents": []}
    if snap is None:
        snap = ps_snapshot()
    e = snap.get(pid)
    if e is None:
        line = run(["/bin/ps", "-p", str(pid), "-o", "user=,ppid=,etime=,comm="]).strip()
        if line:
            f = line.split(None, 3)
            if len(f) >= 4:
                info["user"], ppid, info["started"], info["comm"] = f[0], f[1], f[2], f[3]
                info["ppid"] = int(ppid) if ppid.isdigit() else None
        info["args"] = run(["/bin/ps", "-p", str(pid), "-o", "args="]).strip()[:500]
    else:
        info.update({k: e[k] for k in ("user", "ppid", "comm", "args", "started")})

    # Elternkette hochlaufen - damit man sieht, WER das ausgeloest hat
    seen, cur = set(), info["ppid"]
    while cur and cur > 1 and cur not in seen and len(info["parents"]) < 6:
        seen.add(cur)
        pe = snap.get(cur)
        if pe is None:
            break
        info["parents"].append({"pid": cur, "user": pe["user"], "comm": pe["comm"]})
        cur = pe["ppid"]
    return info


def proc_connections(pid, limit=12, timeout=8):
    """Offene Verbindungen des Prozesses - zeigt WOHIN der Traffic ging."""
    out = run(["/usr/sbin/lsof", "-nP", "-i", "-a", "-p", str(pid)], timeout=timeout)
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
    seen, uniq = set(), []
    for c in conns:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def proc_start_key(pid):
    """Absolute Startzeit - erkennt spaeter wiederverwendete PIDs."""
    return run(["/bin/ps", "-p", str(pid), "-o", "lstart="]).strip()


# ---------------------------------------------------------------- Logging


def _chown_to_invoker(path):
    """
    Laeuft netguard unter sudo, gehoerten neue Logdateien sonst root - und der
    eigentliche Benutzer kaeme an sein eigenes Logverzeichnis (0700) nicht mehr
    heran. Deshalb zurueck an den User, der sudo aufgerufen hat.
    """
    if os.geteuid() != 0:
        return
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if not (uid and uid.isdigit()):
        return
    try:
        os.chown(path, int(uid), int(gid) if gid and gid.isdigit() else -1)
    except OSError:
        pass


class Logger:
    def __init__(self, logdir):
        self.dir = logdir
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)   # Logs enthalten komplette Kommandozeilen
        except OSError:
            pass
        _chown_to_invoker(self.dir)
        self.samples = os.path.join(self.dir, "samples.jsonl")
        self.incidents = os.path.join(self.dir, "incidents.jsonl")
        self.state_file = os.path.join(self.dir, "state.json")

    def _append(self, path, obj):
        is_new = not os.path.exists(path)
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())   # damit nach einem Hard-Cut nichts fehlt
            if is_new:
                _chown_to_invoker(path)
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
        """Atomar - ein abgeschnittenes state.json macht unblock unmoeglich."""
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_file)
            # os.replace legt eine neue Datei an - der Besitzer faellt sonst
            # bei jedem Schreiben auf root zurueck, nicht nur beim ersten Mal.
            _chown_to_invoker(self.state_file)
        except OSError:
            pass

    def rotate(self, max_bytes=50 * 1024 * 1024, keep=3):
        for path in (self.samples, self.incidents):
            try:
                if os.path.exists(path) and os.path.getsize(path) > max_bytes:
                    for i in range(keep - 1, 0, -1):
                        if os.path.exists(f"{path}.{i}"):
                            os.replace(f"{path}.{i}", f"{path}.{i + 1}")
                    os.replace(path, path + ".1")
            except OSError:
                pass


# ---------------------------------------------------------------- Warnstufen

STAGE_ACTIONS = ("notify", "suspend", "kill", "iface", "wifi", "pf")


class Stage:
    """Eine Warnstufe: ab X MB im Fenster passiert Y."""

    def __init__(self, mb, action="notify", sound="Sosumi", repeat=3, say="",
                 volume=1.5):
        if action not in STAGE_ACTIONS:
            raise ValueError(f"unbekannte Aktion '{action}' "
                             f"(erlaubt: {', '.join(STAGE_ACTIONS)})")
        self.mb = float(mb)
        self.limit = int(self.mb * 1024 * 1024)
        self.action = action
        self.sound = sound
        self.repeat = int(repeat)
        self.say = say
        self.volume = float(volume)

    def describe(self):
        ton = "ohne Ton" if not self.sound or self.sound.lower() == "none" \
            else f"{self.sound} x{self.repeat}"
        return (f"ab {self.mb:g} MB -> {self.action} ({ton}"
                + (f", Ansage '{self.say}'" if self.say else "") + ")")


def parse_stage(spec):
    """--stage 'MB:AKTION[:SOUND[:WIEDERHOLUNGEN[:ANSAGE]]]'"""
    parts = spec.split(":", 4)
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            f"--stage braucht mindestens MB:AKTION, bekommen: {spec!r}")
    try:
        mb = float(parts[0])
    except ValueError:
        raise argparse.ArgumentTypeError(f"MB ist keine Zahl: {parts[0]!r}")
    kw = {"action": parts[1]}
    if len(parts) > 2 and parts[2]:
        kw["sound"] = parts[2]
    if len(parts) > 3 and parts[3]:
        try:
            kw["repeat"] = int(parts[3])
        except ValueError:
            raise argparse.ArgumentTypeError(f"Wiederholungen: {parts[3]!r}")
    if len(parts) > 4:
        kw["say"] = parts[4]
    try:
        return Stage(mb, **kw)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


# ---------------------------------------------------------------- Aktionen (Blocken)

PF_ANCHOR_FILE = "/etc/pf.anchors/netguard"


def _is_protected(name, comm=""):
    """
    nettop kuerzt Prozessnamen auf etwa 15 Zeichen ("softwareupdate"), die
    Schutzliste enthaelt die vollen. Deshalb zusaetzlich ein Praefixvergleich
    - und der ungekuerzte comm-Name aus ps, wo er vorliegt.
    """
    cands = {name, os.path.basename(name), os.path.basename(comm)} - {""}
    if cands & NEVER_SUSPEND:
        return True
    return any(len(c) >= 10 and any(p.startswith(c) for p in NEVER_SUSPEND)
               for c in cands)


def _targets(procs, limit, snap, total=0, min_share=MIN_SUSPEND_SHARE):
    """
    Wen Stufe 2 und 3 anfassen duerfen. Nie netguard selbst, nie PID 0/1, nie
    etwas Geschuetztes - und nur, wer im Fenster wirklich ins Gewicht faellt:
    unter `min_share` Anteil bleibt ein Prozess unbehelligt, damit nicht der
    Videocall mit 500 KB neben dem 30-MB-Download eingefroren wird.
    Traegt niemand genug bei, trifft es den groessten allein - sonst waere
    die Stufe wirkungslos.
    """
    own = {os.getpid(), os.getppid()}
    eligible = [(n, p, b) for n, p, b in procs
                if p > 1 and p not in own and b > 0
                and not _is_protected(n, (snap or {}).get(p, {}).get("comm", ""))]
    if not eligible:
        return []
    floor = total * min_share if total > 0 else 0
    picked = [e for e in eligible if e[2] >= floor][:limit]
    if not picked:
        picked = eligible[:1]
    return [(n, p) for n, p, _ in picked]


def _remember(logger, key, entries):
    st = logger.read_state()
    st.setdefault(key, []).extend(entries)
    st["blocked_at"] = now_iso()
    logger.write_state(st)


def act_suspend(procs, logger, limit=3, snap=None, total=0):
    """Top-Verursacher mit SIGSTOP einfrieren (reversibel via SIGCONT)."""
    stopped = []
    for name, pid in _targets(procs, limit, snap, total):
        try:
            start = proc_start_key(pid)
            os.kill(pid, signal.SIGSTOP)
            stopped.append({"name": name, "pid": pid, "start": start, "ts": now_iso()})
        except (ProcessLookupError, PermissionError) as e:
            print(f"[netguard] konnte {name}.{pid} nicht anhalten: {e}", file=sys.stderr)
    if stopped:
        _remember(logger, "suspended", stopped)
    return stopped


def act_kill(procs, logger, limit=3, snap=None, total=0, grace=3.0):
    """
    SIGTERM, nach einer Gnadenfrist SIGKILL. Unwiderruflich - laufende
    Uploads oder ungesicherte Arbeit koennen dabei verloren gehen.
    """
    killed = []
    for name, pid in _targets(procs, limit, snap, total):
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append({"name": name, "pid": pid, "signal": "TERM", "ts": now_iso()})
        except (ProcessLookupError, PermissionError) as e:
            print(f"[netguard] konnte {name}.{pid} nicht beenden: {e}", file=sys.stderr)
    if killed:
        time.sleep(grace)
        for k in killed:
            try:
                os.kill(k["pid"], 0)          # lebt noch?
                os.kill(k["pid"], signal.SIGKILL)
                k["signal"] = "KILL"
            except (ProcessLookupError, PermissionError):
                pass
        _remember(logger, "killed", killed)
    return killed


def act_iface_down(ifname, logger):
    subprocess.run(["/sbin/ifconfig", ifname, "down"], capture_output=True)
    st = logger.read_state()
    st["iface_down"] = ifname
    st["blocked_at"] = now_iso()
    logger.write_state(st)
    return [{"iface": ifname}]


def act_wifi_off(ifname, logger):
    dev = wifi_device() or ifname
    subprocess.run(["/usr/sbin/networksetup", "-setairportpower", dev, "off"],
                   capture_output=True)
    st = logger.read_state()
    st["wifi_off"] = dev
    st["blocked_at"] = now_iso()
    logger.write_state(st)
    return [{"wifi": ifname}]


def act_pf_block(ifname, logger, flush_states=True):
    """
    Kill-Switch ueber die pf-Firewall.
    Voraussetzung: einmalig `sudo netguard.py install-pf` ausgefuehrt.

    pf blockt nur neue Pakete - bestehende States laufen weiter, der grosse
    Download also auch. Deshalb werden die States geflusht (systemweit;
    andere Verbindungen bauen sich einfach neu auf). --pf-keep-states aus.
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
    # pfctl -E zaehlt Aktivierungen hoch und gibt ein Token aus, das beim
    # Deaktivieren wieder zurueckgegeben werden muss - sonst bleibt pf an.
    p = subprocess.run(["/sbin/pfctl", "-E"], capture_output=True, text=True)
    m = re.search(r"Token\s*:\s*(\d+)", (p.stdout or "") + (p.stderr or ""))
    if flush_states:
        subprocess.run(["/sbin/pfctl", "-F", "states"], capture_output=True)
    st = logger.read_state()
    st["pf_blocked"] = ifname
    if m:
        st.setdefault("pf_tokens", []).append(m.group(1))
    st["blocked_at"] = now_iso()
    logger.write_state(st)
    return [{"pf": ifname, "token": m.group(1) if m else None}]


def resume_suspended(logger, verbose=True):
    """Nur die angehaltenen Prozesse freigeben (auch beim Beenden)."""
    st = logger.read_state()
    done = []
    for p in st.get("suspended", []):
        pid = p.get("pid")
        if not pid:
            continue
        # PID koennte inzwischen einem anderen Prozess gehoeren
        if p.get("start") and proc_start_key(pid) not in ("", p["start"]):
            done.append(f"uebersprungen: PID {pid} ist nicht mehr {p.get('name')}")
            continue
        try:
            os.kill(pid, signal.SIGCONT)
            done.append(f"SIGCONT {p.get('name')}.{pid}")
        except (ProcessLookupError, PermissionError):
            pass
    if st.pop("suspended", None) is not None:
        logger.write_state(st)
    if verbose:
        for d in done:
            print("  -", d)
    return done


def do_unblock(logger, verbose=True):
    """Alles wieder freigeben."""
    done = resume_suspended(logger, verbose=False)
    st = logger.read_state()

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
    for tok in st.pop("pf_tokens", []):
        subprocess.run(["/sbin/pfctl", "-X", tok], capture_output=True)
        done.append(f"pf-Token {tok} zurueckgegeben")

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
        self.sim = float(getattr(args, "simulate", 0.0) or 0.0)
        if self.sim:
            # Testlaeufe duerfen die echten Logs und vor allem das
            # Tagesvolumen nicht verfaelschen.
            args.logdir = os.path.join(args.logdir, "simulation")
        self.iface = args.iface or default_interface()
        self.iface_fixed = bool(args.iface)
        self.log = Logger(args.logdir)
        self.window = deque()          # (mono_ts, delta_bytes, {proc: bytes})
        self.prev_raw = None
        self.day = datetime.now().strftime("%Y-%m-%d")
        self.day_bytes = 0
        self.level = 0                 # erreichte Warnstufe
        self.level_time = 0.0
        self.stop_requested = False
        self.cleaned_up = False
        self.mode = "diff" if getattr(args, "attrib", "delta") == "diff" else "delta"
        self.nettop_type = args.nettop_type or default_nettop_type(self.iface)
        self.implausible = 0
        self.warned_no_delta = False
        self.last_warn = 0.0
        # 'netguard unblock' laeuft als eigener Prozess; state.json ist der
        # einzige Kanal, ueber den der Monitor davon erfaehrt.
        self.last_unblock = self.log.read_state().get("unblocked_at")
        self.daily_override = False
        self.stages = self._build_stages()
        self._restore_day_total()

    def _build_stages(self):
        """--stage ... wenn angegeben, sonst eine einzelne Stufe aus --burst-mb."""
        a = self.a
        stages = list(getattr(a, "stage", None) or [])
        if not stages:
            stages = [Stage(a.burst_mb, a.action, a.sound, a.sound_repeat,
                            a.say, a.sound_volume)]
        stages.sort(key=lambda s: s.limit)
        return stages

    # -- Zustand -------------------------------------------------------------

    def _restore_day_total(self):
        """Tagesvolumen ueberlebt Neustart/Respawn - sonst ist das Limit wertlos."""
        st = self.log.read_state()
        if st.get("day") == self.day:
            self.day_bytes = int(st.get("day_bytes", 0))
            if self.day_bytes:
                print(f"[netguard] heute bereits gezaehlt: {human(self.day_bytes)}")

    def _persist_day_total(self):
        st = self.log.read_state()
        st["day"], st["day_bytes"] = self.day, self.day_bytes
        self.log.write_state(st)

    def rollover_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.day_bytes = today, 0
            self.daily_override = False
            self._persist_day_total()

    def maybe_switch_interface(self):
        """Default-Route kann wechseln (WLAN -> Ethernet, VPN an/aus)."""
        if self.iface_fixed or self.sim:
            return
        cur = default_interface()
        if cur and cur != self.iface:
            print(f"[netguard] Interface gewechselt: {self.iface} -> {cur}")
            self.iface = cur
            self.nettop_type = self.a.nettop_type or default_nettop_type(cur)
            self.window.clear()

    def check_unblocked(self):
        """
        Hat jemand entsperrt? Ohne diesen Blick bliebe der Monitor auf seiner
        Stufe stehen: nach Stufe 3 plus unblock liefe der Download bis zum
        Ende des Cooldowns unbeobachtet weiter, mit Tageslimit sogar bis
        Mitternacht.
        """
        if not self.level:
            return
        ts = self.log.read_state().get("unblocked_at")
        if not ts or ts == self.last_unblock:
            return
        self.last_unblock = ts
        self.level = 0
        self.window.clear()      # sonst loest das alte Fenster sofort neu aus
        limit = self.a.daily_mb * 1024 * 1024 if self.a.daily_mb else 0
        if limit and self.day_bytes >= limit:
            # Wer nach dem Tageslimit entsperrt, will bewusst weitermachen -
            # sonst wuerde die naechste Messung sofort wieder sperren.
            self.daily_override = True
            print("[netguard] unblock erkannt - Tageslimit fuer heute ausgesetzt, "
                  "die Burst-Stufen bleiben scharf.")
        else:
            print("[netguard] unblock erkannt - wieder scharf ab Stufe 1.")

    # -- Fenster-Buchhaltung

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

    # -- Attribution ---------------------------------------------------------

    def attribute(self, raw, ok):
        """nettop-Sample in Bytes pro Prozess umrechnen (je nach Modus)."""
        if self.mode == "delta":
            if not ok:
                if not self.warned_no_delta:
                    print("[netguard] nettop liefert kein zweites Sample - "
                          "Attribution fuer diesen Zyklus unbekannt. Haelt das an: "
                          "--attrib diff.", file=sys.stderr)
                    self.warned_no_delta = True
                self.prev_raw = raw
                return {}
            self.prev_raw = raw
            return {k: bi + bo for k, (bi, bo) in raw.items() if bi or bo}
        # Nur auf ausdrueckliche Anweisung: kumulative Werte gegen den
        # vorigen Zyklus diffen. Schliesst waehrenddessen ein Socket, faellt
        # sein Anteil aus der Summe und der Prozess bekommt sein gesamtes
        # bisheriges Volumen als "Delta" zugeschrieben.
        prev, self.prev_raw = self.prev_raw, raw
        if prev is None:
            return {}
        return {k: di + do for k, (di, do) in diff_procs(prev, raw).items()}

    def warn_if_implausible(self, attributed, total_delta):
        """
        Frueher wurde hier automatisch auf kumulatives Diffen umgeschaltet.
        Das war ein Fehlgriff: ausgeloest hat es der Parserfehler aus 268429c,
        und im Diff-Modus wird bei schliessenden Sockets der falsche Prozess
        zum groessten Verursacher - und damit eingefroren. Jetzt gibt es nur
        noch einen Hinweis, die Entscheidung trifft --attrib.
        """
        if self.mode != "delta" or total_delta <= 1024 * 1024:
            self.implausible = 0
            return
        if attributed > IMPLAUSIBLE_FACTOR * total_delta + IMPLAUSIBLE_SLACK:
            self.implausible += 1
            now = time.monotonic()
            if self.implausible >= IMPLAUSIBLE_STREAK and now - self.last_warn > 600:
                self.last_warn = now
                print(f"[netguard] Zuordnung unplausibel: {human(attributed)} auf "
                      f"Prozesse verteilt, aber nur {human(total_delta)} am "
                      f"Interface. Laeuft Verkehr ueber ein anderes Interface "
                      f"(Loopback, AirDrop, VPN)?", file=sys.stderr)
        else:
            self.implausible = 0

    # -- Messung -------------------------------------------------------------

    def measure(self):
        """Ein Messzyklus. Dauert etwa --interval Sekunden."""
        m0, w0 = time.monotonic(), time.time()
        if self.sim:
            time.sleep(max(0.2, self.a.interval))
            m1 = time.monotonic()
            total = int(self.sim * 1024 * 1024 * (m1 - m0))
            return {"bytes": total, "procs": {(SIM_NAME, os.getpid()): total},
                    "residual": 0, "gap": False, "now": m1, "seconds": m1 - m0}

        # netstat direkt um das nettop-Fenster herum -> gleiche Zeitbasis
        i0 = iface_counters(self.iface)
        raw, ok = nettop_sample(self.a.interval, self.nettop_type,
                                delta=(self.mode == "delta"))
        i1 = iface_counters(self.iface)
        m1, w1 = time.monotonic(), time.time()

        total = 0
        if i0 and i1:
            # negativ = Interface-Reset (down/up) -> ignorieren
            total = max(0, i1[0] - i0[0]) + max(0, i1[1] - i0[1])
        procs = self.attribute(raw, ok)
        attributed = sum(procs.values())
        self.warn_if_implausible(attributed, total)
        # Standby oder haengender Aufruf: der Zaehlerstand deckt dann eine viel
        # laengere Zeit ab als ein Fenster - nicht als Burst werten.
        gap_limit = 3 * self.a.interval + 10
        gap = (m1 - m0) > gap_limit or (w1 - w0) > gap_limit
        return {"bytes": total, "procs": procs, "ok": ok, "gap": gap, "now": m1,
                "seconds": m1 - m0, "residual": max(0, total - attributed)}

    # -- Incident ------------------------------------------------------------

    def build_incident(self, level, stage, reason, amount, top, result, residual,
                       skipped=0, snap=None):
        """Langsamer Teil (ps/lsof) - laeuft erst NACH der Aktion."""
        snap = snap if snap is not None else ps_snapshot()
        culprits = []
        for name, pid, byts in top:
            if byts <= 0:
                continue
            entry = {"process": name, "pid": pid, "bytes": byts, "human": human(byts)}
            entry.update(proc_info(pid, snap))
            entry["connections"] = [] if self.sim else proc_connections(pid)
            culprits.append(entry)
        return {
            "ts": now_iso(),
            "type": "incident",
            "simulated": bool(self.sim),
            "stage": level,
            "stages_total": len(self.stages),
            "stage_mb": stage.mb,
            "skipped_stages": skipped,
            "reason": reason,
            "interface": self.iface,
            "window_seconds": self.a.window,
            "bytes": amount,
            "human": human(amount),
            "unattributed_bytes": residual,
            "attribution_mode": self.mode,
            "day_total_bytes": self.day_bytes,
            "day_total_human": human(self.day_bytes),
            "action": stage.action,
            "action_result": result,
            "culprits": culprits,
        }

    def print_incident(self, inc):
        mark = "[SIMULATION] " if inc.get("simulated") else ""
        print("\n" + "=" * 72)
        print(f"!! {mark}STUFE {inc['stage']}/{inc['stages_total']} - {inc['reason']}")
        print(f"   {inc['human']} in {inc['window_seconds']}s auf {inc['interface']}"
              f"  um {inc['ts']}")
        if inc.get("skipped_stages"):
            print(f"   ({inc['skipped_stages']} Stufe(n) uebersprungen - es ging zu schnell)")
        print(f"   heute gesamt: {inc['day_total_human']}")
        if inc.get("unattributed_bytes"):
            print(f"   nicht zuordenbar: {human(inc['unattributed_bytes'])}")
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

    def execute_action(self, stage, top, snap=None):
        """Nur blocken - so schnell wie moeglich, ohne Forensik davor."""
        act = stage.action
        total = self.window_total()
        if self.sim:
            names = ", ".join(f"{n}.{p}" for n, p, _ in top[:3]) or "-"
            print(f"   [SIMULATION] wuerde jetzt '{act}' ausfuehren ({names}) - "
                  f"es passiert nichts.")
            return [{"dry_run": act}]
        if act == "suspend":
            return act_suspend(top, self.log, snap=snap, total=total)
        if act == "kill":
            return act_kill(top, self.log, snap=snap, total=total)
        if act == "iface":
            return act_iface_down(self.iface, self.log)
        if act == "wifi":
            return act_wifi_off(self.iface, self.log)
        if act == "pf":
            return act_pf_block(self.iface, self.log,
                                flush_states=not self.a.pf_keep_states)
        return []

    def handle_trip(self, level, stage, reason, amount, residual, skipped=0):
        top = self.window_top()
        # Der ps-Schnappschuss muss VOR die Aktion: er kostet nur zwei
        # ps-Aufrufe und entscheidet mit, wen wir anfassen duerfen - nettop
        # kuerzt Prozessnamen, die Schutzliste braucht die vollen. Teuer ist
        # das lsof in build_incident, und das laeuft weiter danach.
        snap = ps_snapshot() if stage.action in ("suspend", "kill") else None
        result = self.execute_action(stage, top, snap=snap)
        play_alert(sound=stage.sound, repeat=stage.repeat, volume=stage.volume,
                   say_text=(stage.say or None))
        names = ", ".join(n for n, _, b in top[:3] if b > 0) or "unbekannt"
        if not self.a.no_notify:
            notify(f"netguard: Stufe {level}/{len(self.stages)}",
                   f"{human(amount)} in {self.a.window}s - {names} [{stage.action}]")
        inc = self.build_incident(level, stage, reason, amount, top, result,
                                  residual, skipped, snap=snap)
        self.log.incident(inc)
        self.print_incident(inc)
        return inc

    # -- Beenden -------------------------------------------------------------

    def install_signal_handlers(self):
        def handler(signum, _frame):
            self.stop_requested = True
            self.cleanup(signum)
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    def cleanup(self, signum=None):
        """Angehaltene Prozesse nie eingefroren zuruecklassen."""
        if self.cleaned_up:
            return
        self.cleaned_up = True
        self._persist_day_total()
        if signum:
            print(f"\n[netguard] Signal {signum} - raeume auf.")
        if self.a.unblock_on_exit:
            do_unblock(self.log)
        else:
            for d in resume_suspended(self.log, verbose=False):
                print("  -", d)

    # -- Hauptschleife -------------------------------------------------------

    def print_header(self):
        if self.sim:
            print("=" * 72)
            print(f"  SIMULATION - {self.sim:g} MB/s vorgetaeuscht")
            print("  Es wird KEIN echter Traffic gemessen und KEINE Aktion")
            print("  ausgefuehrt. Toene und Meldungen sind echt.")
            print(f"  Testlogs: {self.log.dir}")
            print("=" * 72)
        print(f"netguard {VERSION} - Interface {self.iface}, "
              f"Sample {self.a.interval}s, Fenster {self.a.window}s")
        for i, s in enumerate(self.stages, 1):
            print(f"  Stufe {i}: {s.describe()}")
        if self.a.daily_mb:
            print(f"  Tageslimit: {self.a.daily_mb:g} MB -> hoechste Stufe")
        print(f"  Logs: {self.log.dir}")
        if os.geteuid() != 0 and not self.sim:
            print("  Hinweis: ohne sudo siehst du nur deine eigenen Prozesse.")
        print("  Strg-C zum Beenden.\n")

    def loop(self):
        self.print_header()
        daily_limit = int(self.a.daily_mb * 1024 * 1024) if self.a.daily_mb else 0
        self.install_signal_handlers()
        started = time.monotonic()
        last_iface_check = last_persist = last_rotate = 0.0

        while not self.stop_requested:
            cycle_start = time.monotonic()
            if cycle_start - last_iface_check > 60:
                self.maybe_switch_interface()
                last_iface_check = cycle_start
            self.rollover_day()
            self.check_unblocked()

            s = self.measure()
            now = s["now"]
            self.day_bytes += s["bytes"]
            if s["gap"]:
                print(f"[netguard] Zeitluecke ({s['seconds']:.0f}s, vermutlich Standby) - "
                      f"{human(s['bytes'])} zaehlen fuer heute, aber nicht fuers Fenster.")
                self.window.clear()
            else:
                self.push(now, s["bytes"], s["procs"])
            win = self.window_total()

            if now - last_persist > 30:
                self._persist_day_total()
                last_persist = now

            # Sample-Log (nur oberhalb eines Rauschbodens, sonst wird die Datei riesig)
            if s["bytes"] >= self.a.log_floor_kb * 1024:
                top5 = sorted(s["procs"].items(), key=lambda kv: kv[1], reverse=True)[:5]
                self.log.sample({
                    "ts": now_iso(), "type": "sample", "iface": self.iface,
                    "simulated": bool(self.sim),
                    "bytes": s["bytes"], "window_bytes": win,
                    "day_bytes": self.day_bytes,
                    "seconds": round(s["seconds"], 2),
                    "unattributed_bytes": s["residual"],
                    "attribution_mode": self.mode,
                    "top": [{"process": k[0], "pid": k[1], "bytes": v} for k, v in top5],
                })

            if self.a.verbose:
                top1 = max(s["procs"].items(), key=lambda kv: kv[1], default=None)
                tag = f"{top1[0][0]}.{top1[0][1]}" if top1 else "-"
                print(f"{datetime.now():%H:%M:%S}  +{human(s['bytes']):>9}  "
                      f"Fenster {human(win):>9}  heute {human(self.day_bytes):>9}  "
                      f"Stufe {self.level}  top: {tag}")

            # --- Warnstufen pruefen
            daily_hit = bool(daily_limit and not self.daily_override
                             and self.day_bytes >= daily_limit)
            reached, amount = 0, win
            for i, stage in enumerate(self.stages):
                if win >= stage.limit:
                    reached = i + 1
            if daily_hit and reached < len(self.stages):
                reached, amount = len(self.stages), self.day_bytes

            if reached > self.level:
                stage = self.stages[reached - 1]
                if daily_hit and amount == self.day_bytes:
                    reason = f"Tageslimit erreicht: {human(self.day_bytes)}"
                else:
                    reason = (f"{human(win)} in {self.a.window}s "
                              f"(Schwelle {stage.mb:g} MB)")
                self.handle_trip(reached, stage, reason, amount, s["residual"],
                                 skipped=reached - self.level - 1)
                self.level, self.level_time = reached, now
                if stage.action in ("iface", "wifi", "pf") and not self.sim:
                    print("Netz ist gesperrt. Freigeben mit:  sudo netguard.py unblock")
                    if self.a.stop_after_trip:
                        break
                if self.sim and reached >= len(self.stages):
                    print("[SIMULATION] hoechste Stufe erreicht - Test beendet.\n")
                    break
            elif self.level and now - self.level_time > self.a.cooldown:
                # Nach Cooldown zurueck auf Stufe 0, wenn es sich beruhigt hat
                if win < self.stages[0].limit and not daily_hit:
                    self.level = 0
                    print(f"[{datetime.now():%H:%M:%S}] zurueck auf Stufe 0 - wieder scharf.")

            if now - last_rotate > 60:
                self.log.rotate()
                last_rotate = now
            if self.a.simulate_seconds and now - started > self.a.simulate_seconds:
                print("[SIMULATION] Zeit abgelaufen - Test beendet.\n")
                break

            # nettop hat das Intervall normalerweise schon verbraucht
            rest = self.a.interval - (time.monotonic() - cycle_start)
            if rest > 0:
                time.sleep(min(rest, self.a.interval))
            elif not self.sim and not s.get("ok") and self.mode == "delta":
                time.sleep(1)   # nettop nicht verfuegbar -> nicht heisslaufen

        self.cleanup()
        return 0


# ---------------------------------------------------------------- Subcommands


def cmd_top(args):
    """Einmal-Messung: wer zieht gerade Daten?"""
    iface = args.iface or default_interface()
    dur = args.duration
    print(f"Messe {dur}s auf {iface} ...")
    i0 = iface_counters(iface)
    raw, ok = nettop_sample(dur, args.nettop_type, delta=True)
    i1 = iface_counters(iface)

    tot = None
    if i0 and i1:
        tot = max(0, i1[0] - i0[0]) + max(0, i1[1] - i0[1])
    attributed = sum(bi + bo for bi, bo in raw.values())
    # Liegt die Summe weit ueber dem, was das Interface gesehen hat, waren es
    # keine Deltas, sondern kumulative Werte -> genauso unbrauchbar.
    if ok and tot is not None and attributed > IMPLAUSIBLE_FACTOR * tot + IMPLAUSIBLE_SLACK:
        print(f"nettop-Werte unplausibel ({human(attributed)} zugeordnet bei "
              f"{human(tot)} am Interface) - messe kumulativ nach ...", file=sys.stderr)
        ok = False

    if not ok:
        # Kein Delta-Modus auf diesem System: zwei kumulative Momentaufnahmen
        # nehmen und selbst diffen. Kostet die Messdauer ein zweites Mal.
        print(f"nettop kennt hier keinen Delta-Modus - messe {dur}s kumulativ nach ...",
              file=sys.stderr)
        i0 = iface_counters(iface)
        p0, _ = nettop_sample(1, args.nettop_type, delta=False)
        time.sleep(dur)
        p1, _ = nettop_sample(1, args.nettop_type, delta=False)
        i1 = iface_counters(iface)
        raw = diff_procs(p0, p1)
    ranked = sorted(((k, v[0] + v[1]) for k, v in raw.items()),
                    key=lambda kv: kv[1], reverse=True)
    attributed = sum(b for _, b in ranked)

    if i0 and i1:
        tot = max(0, i1[0] - i0[0]) + max(0, i1[1] - i0[1])
        print(f"\nInterface {iface} gesamt: {human(tot)}  ({human(tot / dur)}/s)")
        print(f"davon zugeordnet: {human(min(attributed, tot))}, "
              f"nicht zuordenbar: {human(max(0, tot - attributed))}\n")

    snap = ps_snapshot()
    print(f"{'Bytes':>11}  {'Rate/s':>10}  Prozess")
    print("-" * 72)
    for (name, pid), b in ranked[:args.limit]:
        if b <= 0:
            continue
        info = proc_info(pid, snap)
        print(f"{human(b):>11}  {human(b / dur):>10}  {name}.{pid}  user={info['user']}")
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
    if st.get("day") == datetime.now().strftime("%Y-%m-%d"):
        print(f"Heute:      {human(st.get('day_bytes', 0))}")
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
        for line in deque(fh, maxlen=args.limit):   # Datei nicht komplett laden
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    for inc in rows:
        print(f"\n{inc['ts']}  {inc['human']} in {inc.get('window_seconds')}s"
              f"  [{inc.get('action')}]")
        print(f"  {inc['reason']}")
        if inc.get("unattributed_bytes"):
            print(f"  nicht zuordenbar: {human(inc['unattributed_bytes'])}")
        for c in inc.get("culprits", [])[:4]:
            print(f"    {c['human']:>10}  {c['process']}.{c['pid']}  user={c['user']}")
            if c.get("connections"):
                print(f"                -> {', '.join(c['connections'][:4])}")
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
    play_alert(args.sound, args.sound_repeat, args.sound_volume, args.say or None)
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
    <string>{python}</string>
    <string>{script}</string>
    <string>--logdir</string><string>{logdir}</string>
{iface}    <string>monitor</string>
    <string>--interval</string><string>{interval}</string>
    <string>--burst-mb</string><string>{burst}</string>
    <string>--window</string><string>{window}</string>
    <string>--daily-mb</string><string>{daily}</string>
    <string>--action</string><string>{action}</string>
    <string>--sound</string><string>{sound}</string>
    <string>--sound-repeat</string><string>{sound_repeat}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <!-- nur bei Absturz neu starten: ein sauberes Ende (etwa nach einem
       harten Block mit stop_after_trip) soll nicht sofort respawnen -->
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{logdir}/netguard.out.log</string>
  <key>StandardErrorPath</key><string>{logdir}/netguard.err.log</string>
</dict>
</plist>
"""


def cmd_install_agent(args):
    script = os.path.abspath(__file__)
    iface = ""
    if args.iface:
        iface = (f'    <string>--iface</string>'
                 f'<string>{xml_escape(args.iface)}</string>\n')
    plist = PLIST.format(
        python=xml_escape(sys.executable or "/usr/bin/python3"),
        script=xml_escape(script), iface=iface,
        logdir=xml_escape(args.logdir), interval=args.interval,
        burst=args.burst_mb, window=args.window, daily=args.daily_mb,
        action=args.action, sound=xml_escape(args.sound),
        sound_repeat=args.sound_repeat)
    target = "/Library/LaunchDaemons/local.netguard.plist"
    if not args.yes:
        print(f"Wuerde schreiben nach {target}:\n")
        print(plist)
        print("Nochmal mit --yes zum Installieren.")
        return 0
    if os.geteuid() != 0:
        print("Bitte mit sudo ausfuehren.")
        return 1
    os.makedirs(args.logdir, mode=0o700, exist_ok=True)
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


def default_logdir():
    return ("/var/log/netguard" if os.geteuid() == 0
            else os.path.expanduser("~/Library/Logs/netguard"))


def add_common(p, suppress=False):
    """
    Globale Optionen. In den Subparsern mit SUPPRESS als Default, damit ein
    vor dem Subkommando gesetzter Wert nicht wieder ueberschrieben wird.
    So funktionieren beide Reihenfolgen:
        netguard.py --logdir X monitor
        netguard.py monitor --logdir X
    """
    d = (lambda v: argparse.SUPPRESS) if suppress else (lambda v: v)
    p.add_argument("--logdir", default=d(default_logdir()))
    p.add_argument("--iface", default=d(None), help="z.B. en0 (Default: Default-Route)")
    p.add_argument("--nettop-type", default=d(None),
                   choices=["wifi", "wired", "loopback", "awdl"],
                   help="nettop auf einen Interface-Typ einschraenken")
    p.add_argument("-v", "--verbose", action="store_true",
                   default=argparse.SUPPRESS if suppress else False)


def build_parser():
    p = argparse.ArgumentParser(
        prog="netguard.py",
        description="Datenverbrauch ueberwachen, Verursacher ermitteln, Bursts blocken.")
    p.add_argument("--version", action="version", version=f"netguard {VERSION}")
    add_common(p)
    sub = p.add_subparsers(dest="cmd", required=True)

    def child(name, **kw):
        c = sub.add_parser(name, **kw)
        add_common(c, suppress=True)
        return c

    m = child("monitor", help="Dauerueberwachung")
    m.add_argument("--interval", type=int, default=5,
                   help="Laenge eines nettop-Samples in s (= Messtakt)")
    m.add_argument("--window", type=int, default=60, help="Burst-Fenster in s")
    m.add_argument("--burst-mb", type=float, default=50.0,
                   help="MB im Fenster, ab denen ausgeloest wird")
    m.add_argument("--daily-mb", type=float, default=0.0,
                   help="optionales Tageslimit in MB (0 = aus)")
    m.add_argument("--action", default="notify", choices=list(STAGE_ACTIONS),
                   help="Aktion, wenn keine --stage angegeben ist")
    m.add_argument("--stage", action="append", type=parse_stage, metavar="SPEC",
                   help="Warnstufe 'MB:AKTION[:SOUND[:ANZAHL[:ANSAGE]]]', "
                        "mehrfach angebbar, z.B. --stage 12:notify:Ping:1 "
                        "--stage 24:suspend:Sosumi:3 --stage 36:wifi:Submarine:5")
    m.add_argument("--simulate", type=float, default=0.0, metavar="MB_PRO_S",
                   help="Testmodus: taeuscht diesen Verbrauch vor. Misst nichts "
                        "Echtes, fuehrt KEINE Aktion aus, loggt separat - "
                        "Toene und Meldungen sind echt.")
    m.add_argument("--simulate-seconds", type=int, default=0, metavar="S",
                   help="Testmodus nach S Sekunden beenden (0 = bis zur "
                        "hoechsten Stufe)")
    m.add_argument("--attrib", default="delta", choices=["delta", "diff"],
                   help="Zuordnung: nettop-Deltas (Default) oder kumulatives "
                        "Diffen zweier Samples. Kein automatischer Wechsel: "
                        "der Diff-Modus kann bei schliessenden Sockets den "
                        "falschen Prozess als Verursacher ausweisen.")
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
    m.add_argument("--unblock-on-exit", action="store_true",
                   help="beim Beenden alles entsperren (sonst werden nur "
                        "angehaltene Prozesse fortgesetzt)")
    m.add_argument("--pf-keep-states", action="store_true",
                   help="bestehende pf-States nicht flushen (laufende "
                        "Downloads laufen dann weiter)")
    m.set_defaults(func=lambda a: Monitor(a).loop())

    ts = child("testsound", help="Alarmton einmal abspielen")
    ts.add_argument("--sound", default="Sosumi")
    ts.add_argument("--sound-repeat", type=int, default=3)
    ts.add_argument("--sound-volume", type=float, default=1.5)
    ts.add_argument("--say", default="")
    ts.set_defaults(func=cmd_testsound)

    t = child("top", help="Momentaufnahme: wer zieht gerade Daten?")
    t.add_argument("--duration", type=int, default=10)
    t.add_argument("--limit", type=int, default=15)
    t.set_defaults(func=cmd_top)

    s = child("status", help="Sperrstatus anzeigen")
    s.set_defaults(func=cmd_status)

    u = child("unblock", help="Sperre aufheben")
    u.set_defaults(func=cmd_unblock)

    r = child("report", help="Incidents zusammenfassen")
    r.add_argument("--limit", type=int, default=20)
    r.set_defaults(func=cmd_report)

    ip = child("install-pf", help="pf-Kill-Switch vorbereiten (einmalig)")
    ip.add_argument("--yes", action="store_true")
    ip.set_defaults(func=cmd_install_pf)

    ia = child("install-agent", help="als LaunchDaemon installieren")
    ia.add_argument("--yes", action="store_true")
    ia.add_argument("--interval", type=int, default=5)
    ia.add_argument("--burst-mb", type=float, default=50.0)
    ia.add_argument("--window", type=int, default=60)
    ia.add_argument("--daily-mb", type=float, default=0.0)
    ia.add_argument("--action", default="notify", choices=list(STAGE_ACTIONS))
    ia.add_argument("--sound", default="Sosumi")
    ia.add_argument("--sound-repeat", type=int, default=3)
    ia.set_defaults(func=cmd_install_agent)

    return p


def main():
    global VERBOSE
    args = build_parser().parse_args()
    VERBOSE = getattr(args, "verbose", False)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
