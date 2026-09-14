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
| `netguard daemon` | Als LaunchDaemon installieren/aktualisieren (Dauerbetrieb, siehe unten) |
| `netguard daemon-status` | Laeuft der Daemon gerade? |
| `netguard daemon-log` | Live mitlesen wie bei `netguard` im Terminal |
| `netguard daemon-errors` | Fehlerausgabe des Daemons (Abstuerze, Tracebacks) |
| `netguard daemon-off` | Daemon stoppen (dauerhaft, siehe unten) |
| `netguard daemon-uninstall` | Daemon komplett entfernen (Plist geloescht) |

`ng` ist ein Alias fuer `netguard` - und gleichzeitig das Kommando der
Angular-CLI. Wer beides braucht, benennt den Alias in `shell/netguard.zsh`
um.

`netguard` ist eine zsh-Funktion, kein Programm im Pfad: `sudo netguard ...`
scheitert mit *command not found*, weil sudo keine Shell-Funktionen kennt.
Die Funktion ruft sudo dort auf, wo es gebraucht wird.

## Die drei Stufen

Gemessen wird in einem gleitenden Fenster von 10 Sekunden:

1. **ab 10 MB** (= 1,0 MB/s anhaltend) - Warnton `Ping`, Mitteilung, sonst nichts
2. **ab 26 MB** (= 2,6 MB/s) - `Sosumi` dreimal, der Verursacher wird mit
   `SIGSTOP` eingefroren. Reversibel: `netguard unblock` setzt ihn fort.
3. **ab 47 MB** (= 4,7 MB/s) - `Submarine` fuenfmal, Sprachansage, Netz aus.

Die Abstaende zwischen den Stufen (16 und 21 MB) sind mit Absicht groesser
als der Zuwachs eines Messtakts: geprueft wird erst, wenn ein Sample fertig
ist, also waechst das Fenster um `Rate x Intervall` auf einmal. Bei Intervall
2 muesste ein Download schneller als 8 MB/s laufen, damit Stufe 1 gar nicht
erst zum Zug kommt.

### Wen Stufe 2 anfasst

Nur wer mindestens **20 % des Fenstervolumens** verursacht hat, hoechstens
drei Prozesse. Der Videocall mit 500 KB bleibt also neben dem 26-MB-Download
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

Beim Beenden (Strg-C, SIGTERM oder SIGHUP - etwa weil das Terminal-Fenster
geschlossen wird) werden eingefrorene Prozesse wieder fortgesetzt - netguard
laesst nichts eingefroren zurueck, wenn es selbst nicht mehr da ist. Ein
abgeschaltetes Netz und ein pf-Block bleiben dagegen bestehen, bis
`netguard unblock` kommt (oder `--unblock-on-exit` gesetzt ist).

## Persistenz - was ueberlebt einen Neustart von netguard

Nichts davon braucht einen Hintergrunddienst: `state.json` und
`incidents.jsonl` liegen so oder so in `netguard/logs/` und ueberleben jedes
Beenden und jeden Neustart von netguard selbst - der Hintergrunddienst
(`netguard daemon`, siehe unten) haette daran nichts geaendert, er haette
nur zusaetzlich dafuer gesorgt, dass ueberhaupt *gemessen* wird, auch ohne
offenes Terminal.

Startest du `netguard` neu, zeigt es jetzt von selbst einen kurzen
Rueckblick: ob noch etwas gesperrt ist (samt angehaltener Prozesse) und was
der letzte protokollierte Vorfall war - ohne dass du extra `netguard
status`/`netguard report` aufrufen musst (die bleiben fuer die volle Liste
bzw. Details natuerlich trotzdem da). Alles menschenlesbar in
`incidents.jsonl` (ein JSON-Objekt pro Zeile, mit `netguard report`
zusammengefasst) und `state.json` (aktueller Sperrzustand).

## Testlauf

```sh
netguard test        # 4 MB/s vorgetaeuscht
netguard test 8      # 8 MB/s
```

Der Testmodus misst nichts Echtes und fuehrt keine Aktion aus - er schreibt
`[SIMULATION] wuerde jetzt ... ausfuehren` und spielt die echten Toene, damit
man Lautstaerke und Abfolge im Voraus hoert. Er loggt in ein eigenes
Unterverzeichnis, damit das gezaehlte Tagesvolumen unberuehrt bleibt. Mit den
Standard-4 MB/s (`netguard test`) faellt Stufe 1 nach etwa 3 s, Stufe 2 nach
etwa 7 s - Stufe 3 (47 MB) wird bei 4 MB/s aber gar nicht erreicht, weil das
10-Sekunden-Fenster bei Dauerlast auf `Rate x Fenster` = 40 MB deckelt, sobald
die ersten Samples wieder herausrutschen. Um auch Stufe 3 zu sehen, hoeher
ansetzen, z. B. `netguard test 6` (60 MB Deckel).

## Wie gemessen wird

* **Volumen**: kumulative Byte-Zaehler des Interfaces (`netstat -bnI`). Das
  ist die Wahrheit ueber den tatsaechlichen Verbrauch.
* **Zuordnung**: `nettop -P -d -L 2 -s <interval>` liefert echte Deltas pro
  Prozess ueber das ganze Intervall. Die Summe wird gegen das netstat-Delta
  plausibilisiert; passt sie dauerhaft nicht, gibt es nur eine
  ratenbegrenzte Warnung - kein automatischer Wechsel mehr. Der kumulative
  Diff-Modus (`--attrib diff`) kann bei schliessenden Sockets den falschen
  Prozess als Verursacher ausweisen und ist deshalb nur manuell waehlbar.
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

## Dauerbetrieb (LaunchDaemon)

```sh
netguard daemon           # installieren/aktualisieren + sofort starten
netguard daemon-status    # laeuft er gerade?
netguard daemon-off       # stoppen, bleibt auch nach einem Neustart aus
```

(`daemon-off` macht intern sowohl `launchctl bootout` - sofort stoppen - als
auch `launchctl disable` - persistent, sonst laedt launchd das Plist beim
naechsten Boot einfach wieder. `netguard daemon` hebt ein vorheriges
`disable` automatisch wieder auf.)

`netguard daemon` uebernimmt automatisch die aktuell in `shell/netguard.zsh`
eingestellten drei Stufen (`NETGUARD_S1_MB` etc.) - dieselbe `--stage`-Syntax
wie beim interaktiven Aufruf, keine gesonderte Konfiguration. Der Daemon
laeuft ab dann bei jedem Systemstart als root im Hintergrund, auch ohne
angemeldeten Benutzer und ohne offenes Terminal; Strg-C gibt es dafuer nicht
mehr, beenden geht nur ueber `netguard daemon-off`.

Die Logs bleiben im selben Repo-Ordner (`netguard/logs/`) wie im
Terminalbetrieb - `netguard status`/`report`/`unblock`/`test` funktionieren
also unveraendert nebenher, sie teilen sich `state.json`. Weil launchd den
Daemon direkt als root startet (kein `sudo`, kein `SUDO_UID`), gehoert das
Log-Verzeichnis anfangs root, bis sich jemand grafisch anmeldet: sobald ein
Benutzer eingeloggt ist, zieht der naechste Schreibzugriff das
Verzeichnis automatisch auf dessen Besitz nach - vor dem ersten Login sind
`netguard status` & Co. also kurzzeitig nur mit `sudo` erreichbar.

Ein `sudo netguard install-agent ...` (bzw. `install-agent --stage ...`) von
Hand geht weiterhin, etwa fuer eine abweichende Konfiguration ausserhalb der
zsh-Defaults - `netguard daemon` ist nur der bequeme Weg mit den aktuellen
Werten.

Laeuft der Daemon dauerhaft, sollte kein zusaetzliches `netguard` (bzw. `ng`,
ohne Subkommando) im Terminal parallel laufen: beide schreiben unabhaengig
voneinander in dieselbe `state.json` (insbesondere das Tagesvolumen
`day_bytes`) und lesen sich dabei nicht gegenseitig - der zuletzt
schreibende Prozess gewinnt, das Tagesvolumen wird also nicht addiert,
sondern verfaelscht. Ausserdem wuerden beide unabhaengig voneinander
denselben Traffic messen und im Zweifel doppelt eskalieren (zwei
ueberlagerte Alarme etc.). `status`/`unblock`/`report`/`test` sind davon
nicht betroffen und bleiben normal nutzbar.

Live mitlesen, was der Daemon gerade sieht - dasselbe Format wie `netguard`
im Terminal, weil `-v` mit in den Plist geschrieben wird:

```sh
netguard daemon-log       # wie 'netguard', nur vom Daemon statt vom Terminal
netguard daemon-errors    # falls der Daemon nicht anspringt: Tracebacks hier
```

### Bekannte Stolperfalle: Desktop-Pfad + Full Disk Access

Liegt das Repo unter `~/Desktop/...` (wie hier per Default), scheitert der
Daemon beim Start mit `Operation not permitted` beim Oeffnen von
`netguard.py` - sichtbar in `netguard daemon-errors`, `daemon-status` zeigt
dann dauerhaft `active count = 0` / `spawn scheduled` (launchd startet ihn
alle `ThrottleInterval`-Sekunden neu und scheitert wieder). Grund: seit
macOS 10.15.4 gilt TCC (Full Disk Access) auch fuer root-Prozesse - ein
LaunchDaemon darf ohne explizite Freigabe nicht in Desktop/Dokumente/
Downloads lesen, ein `sudo` aus dem Terminal dagegen schon (Terminal.app
hat die Freigabe meist laengst, und die vererbt sich an Kindprozesse).

Zwei Wege, das zu beheben:

1. **Empfohlen - Repo aus dem TCC-geschuetzten Ordner verschieben**, z. B.
   nach `~/dev-tools` (direkt im Home-Verzeichnis, nicht unter Desktop/
   Dokumente/Downloads). Danach in `~/.zshrc` den `source`-Pfad anpassen
   und `netguard daemon` neu ausfuehren. Kein Full-Disk-Access-Grant noetig.
2. **Alternativ - Full Disk Access fuer den Python-Interpreter erteilen**:
   Systemeinstellungen -> Datenschutz & Sicherheit -> Vollstaendiger
   Festplattenzugriff -> `+` -> exakt den Pfad aus `program =` in `netguard daemon-status`
   hinzufuegen (z. B. `/Library/Frameworks/Python.framework/Versions/3.14/
   Resources/Python.app`). Nachteil: das gilt fuer diesen Python-Interpreter
   insgesamt, nicht nur fuer netguard - eine deutlich groessere Freigabe als
   noetig.

## Optional: pf-Kill-Switch

```sh
sudo ./netguard.py install-pf --yes
```

Danach ist z. B. `--stage 47:pf` moeglich (anstelle von `47:wifi`): blockt
alles auf dem Interface ueber die pf-Firewall und flusht bestehende
Verbindungen, damit ein laufender Download wirklich stoppt. `netguard unblock` leert den Anchor und gibt das
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
