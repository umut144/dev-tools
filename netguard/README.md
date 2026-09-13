# netguard

Datenverbrauchs-Waechter fuer macOS. Misst den echten Traffic des
Netzwerk-Interfaces, ordnet ihn Prozessen zu und eskaliert in drei Stufen,
wenn zu viel durchgeht. Nur Python-Standardbibliothek, keine Abhaengigkeiten.

## Installation

```sh
echo 'source ~/Desktop/dev-tools/netguard/shell/netguard.zsh' >> ~/.zshrc
exec zsh
```

## Bedienung

| Befehl | Wirkung |
|---|---|
| `netguard` | Ueberwachung starten (sudo, Strg-C beendet) |
| `netguard test [MB/s]` | Trockenlauf, Standard 4 MB/s, ohne echten Verbrauch |
| `netguard top -v` | Momentaufnahme: wer zieht gerade Daten? |
| `netguard status` | Sperrstatus + heutiges Volumen |
| `netguard unblock` | Alles freigeben |
| `netguard report` | Die letzten Vorfaelle |
| `netguard sound Basso` | Einen Systemsound probehoeren |

`ng` ist ein Alias fuer `netguard`.

## Die drei Stufen

Gemessen wird in einem gleitenden Fenster von 10 Sekunden:

1. **ab 12 MB** (= 1,2 MB/s anhaltend) - Warnton `Ping`, Mitteilung, sonst nichts
2. **ab 24 MB** (= 2,4 MB/s) - `Sosumi` dreimal, der groesste Verursacher wird
   mit `SIGSTOP` eingefroren. Reversibel: `netguard unblock` setzt ihn fort.
3. **ab 36 MB** (= 3,6 MB/s) - `Submarine` fuenfmal, Sprachansage, WLAN aus.

Jede Stufe loest nur einmal aus. Beruhigt sich der Verbrauch fuer die Dauer
von `--cooldown` (Standard 300 s) unter Stufe 1, ist netguard wieder scharf.
Geht es so schnell, dass mehrere Schwellen in einem Messtakt fallen, wird nur
die hoechste erreichte Stufe ausgefuehrt und das im Log vermerkt.

Nie eingefroren werden: netguard selbst, PID 0/1 und die Liste in
`NEVER_SUSPEND` (WindowServer, configd, mDNSResponder, Terminal, sshd, ...).
Ist der groesste Verursacher geschuetzt, geht netguard die Liste weiter
runter, statt gar nichts zu tun.

### Die Schwellen im Alltag

| Was | Verbrauch | Reaktion |
|---|---|---|
| Spotify | ~0,04 MB/s | nichts |
| Videocall (Zoom/FaceTime) | 0,2-0,4 MB/s | nichts |
| Netflix HD | ~0,6 MB/s | Stufe 1 |
| Download / Systemupdate | 2-10 MB/s | Stufe 2-3 in Sekunden |

Wer die Stufen auf ein 60-Sekunden-Fenster zieht, trifft damit auch normale
Videocalls. Fenster und Schwellen stehen oben in `shell/netguard.zsh`.

## Testlauf

```sh
netguard test        # 4 MB/s vorgetaeuscht
netguard test 8      # 8 MB/s
```

Der Testmodus misst nichts Echtes und fuehrt keine Aktion aus - er schreibt
`[SIMULATION] wuerde jetzt ... ausfuehren` und spielt die echten Toene, damit
man Lautstaerke und Abfolge im Voraus hoert. Er loggt in ein eigenes
Unterverzeichnis, damit das gezaehlte Tagesvolumen unberuehrt bleibt. Mit
4 MB/s faellt Stufe 1 nach 3 s, Stufe 2 nach 6 s, Stufe 3 nach 9 s.

## Wie gemessen wird

* **Volumen**: kumulative Byte-Zaehler des Interfaces (`netstat -bnI`). Das
  ist die Wahrheit ueber den tatsaechlichen Verbrauch.
* **Zuordnung**: `nettop -P -d -L 2 -s <interval>` liefert echte Deltas pro
  Prozess ueber das ganze Intervall. Die Summe wird gegen das netstat-Delta
  plausibilisiert; passt sie dauerhaft nicht, schaltet netguard selbst auf
  den kumulativen Diff-Modus um (`--attrib delta|diff` erzwingt eine Variante).
* Bei einem Vorfall wird zuerst geblockt und alarmiert, danach erst die
  Forensik (`ps`-Elternkette, offene Verbindungen via `lsof`) erhoben.
* Standby- und Haenger-Luecken werden erkannt und nicht als Burst gewertet.

## Logs

Standard `/var/log/netguard` (Rechte 0700, enthaelt vollstaendige
Kommandozeilen):

* `samples.jsonl` - Messpunkte oberhalb des Rauschbodens
* `incidents.jsonl` - Vorfaelle samt Verursacher und Zielen
* `state.json` - was gerade gesperrt ist, Tagesvolumen

## Dauerbetrieb

```sh
sudo ./netguard.py --logdir /var/log/netguard install-agent \
     --burst-mb 24 --window 10 --action suspend        # zeigt das Plist
sudo ./netguard.py ... install-agent ... --yes         # installiert es
```

Der LaunchDaemon startet bei jedem Boot. Stoppen:
`sudo launchctl bootout system /Library/LaunchDaemons/local.netguard.plist`.
Mehrstufige Konfigurationen gehen dort noch nicht - der Agent faehrt eine
einzelne Schwelle.

## Optional: pf-Kill-Switch

```sh
sudo ./netguard.py install-pf --yes
```

Danach ist `--stage 36:pf` moeglich: blockt alles auf dem Interface ueber die
pf-Firewall und flusht bestehende Verbindungen, damit ein laufender Download
wirklich stoppt. `netguard unblock` leert den Anchor und gibt das
pf-Enable-Token zurueck.
