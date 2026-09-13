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

`ng` ist ein Alias fuer `netguard` - und gleichzeitig das Kommando der
Angular-CLI. Wer beides braucht, benennt den Alias in `shell/netguard.zsh`
um.

`netguard` ist eine zsh-Funktion, kein Programm im Pfad: `sudo netguard ...`
scheitert mit *command not found*, weil sudo keine Shell-Funktionen kennt.
Die Funktion ruft sudo dort auf, wo es gebraucht wird.

## Die drei Stufen

Gemessen wird in einem gleitenden Fenster von 10 Sekunden:

1. **ab 12 MB** (= 1,2 MB/s anhaltend) - Warnton `Ping`, Mitteilung, sonst nichts
2. **ab 30 MB** (= 3,0 MB/s) - `Sosumi` dreimal, der Verursacher wird mit
   `SIGSTOP` eingefroren. Reversibel: `netguard unblock` setzt ihn fort.
3. **ab 55 MB** (= 5,5 MB/s) - `Submarine` fuenfmal, Sprachansage, Netz aus.

Die Abstaende zwischen den Stufen (18 und 25 MB) sind mit Absicht groesser
als der Zuwachs eines Messtakts: geprueft wird erst, wenn ein Sample fertig
ist, also waechst das Fenster um `Rate x Intervall` auf einmal. Bei Intervall
2 muesste ein Download schneller als 9 MB/s laufen, damit Stufe 1 gar nicht
erst zum Zug kommt.

### Wen Stufe 2 anfasst

Nur wer mindestens **20 % des Fenstervolumens** verursacht hat, hoechstens
drei Prozesse. Der Videocall mit 500 KB bleibt also neben dem 30-MB-Download
unbehelligt. Traegt niemand so viel bei - Verkehr gleichmaessig auf viele
Prozesse verteilt -, trifft es den groessten allein, sonst waere die Stufe
wirkungslos.

Nie angefasst werden netguard selbst, PID 0/1 und die Liste `NEVER_SUSPEND`:
WindowServer, configd, mDNSResponder, cfprefsd, runningboardd, fileproviderd
und andere, deren Einfrieren die Oberflaeche oder das Prozessmanagement
haengen laesst. Weil nettop Prozessnamen auf etwa 15 Zeichen kuerzt, wird
zusaetzlich per Praefix und gegen den vollen Namen aus `ps` verglichen.

Mit auf der Liste stehen `cloudd` und `bird`, also iCloud. Das ist eine
bewusste Abwaegung: eine ausser Kontrolle geratene iCloud-Synchronisation
kann Stufe 2 damit nicht bremsen - dafuer ist Stufe 3 zustaendig. Stufe 1
nennt den Verursacher trotzdem beim Namen, du kannst also selbst eingreifen.

### Wann es wieder scharf ist

Jede Stufe loest nur einmal aus. Beruhigt sich der Verbrauch fuer die Dauer
von `--cooldown` (Standard 300 s) unter Stufe 1, faellt netguard auf Stufe 0
zurueck. `netguard unblock` wirkt sofort: der laufende Monitor sieht es an
`state.json` und ist wieder ab Stufe 1 scharf, statt bis zum Ende des
Cooldowns stumm zu bleiben. Wer nach einem erreichten **Tageslimit**
entsperrt, will bewusst weitermachen - das Tageslimit ist dann fuer diesen
Tag ausgesetzt, die Burst-Stufen bleiben aktiv.

Beim Beenden (Strg-C oder SIGTERM) werden eingefrorene Prozesse wieder
fortgesetzt - netguard laesst nichts eingefroren zurueck, wenn es selbst
nicht mehr da ist. Ein abgeschaltetes Netz und ein pf-Block bleiben dagegen
bestehen, bis `netguard unblock` kommt (oder `--unblock-on-exit` gesetzt ist).

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

Liegen in `netguard/logs/` direkt im Repo (Rechte 0700):

* `samples.jsonl` - Messpunkte oberhalb des Rauschbodens
* `incidents.jsonl` - Vorfaelle samt Verursacher und Zielen
* `state.json` - was gerade gesperrt ist, Tagesvolumen
* `simulation/` - dasselbe fuer Testlaeufe, getrennt gehalten

Der Inhalt ist per `.gitignore` ausgeschlossen und sollte es bleiben: die
Vorfaelle enthalten vollstaendige Kommandozeilen, und in denen stehen gern
mal Tokens oder interne URLs. Laeuft der Monitor unter `sudo`, werden neu
angelegte Dateien an den aufrufenden Benutzer zurueckgegeben - sonst
gehoerten sie root und waeren in einem 0700-Verzeichnis fuer dich selbst
nicht mehr lesbar.

Rotiert wird bei 50 MB je Datei, drei Generationen (`.1` bis `.3`).

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

## Zur sudo-Grenze

Der Monitor laeuft als root, das Skript und der benutzte Python-Interpreter
liegen aber in Benutzerhand. Wer den Benutzeraccount kontrolliert, kann den
Inhalt von `netguard.py` aendern und bekommt ihn beim naechsten `netguard`
mit root-Rechten ausgefuehrt - erst recht als LaunchDaemon. Die sudo-Grenze
ist hier also nominell. Auf einem Einzelplatzgeraet ist das vertretbar; auf
einem Rechner mit mehreren Benutzern gehoerten Skript und Interpreter in
root-eigene, nur fuer root beschreibbare Pfade.

Die Logs im Repo-Ordner sind davon nicht betroffen: sie gehoeren dem
Benutzer, liegen unter 0700 und werden nicht mitversioniert.
