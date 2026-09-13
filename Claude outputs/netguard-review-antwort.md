**Betreff:** Re: Code-Review netguard – Ergebnis

Hallo,

das Review ist durch. Ich habe den Code, die Historie und die zsh-Integration gelesen, die Eskalations- und Fallback-Logik mit gestubbten Systemaufrufen unter Python 3.9 und 3.14 nachgespielt und die macOS-spezifischen Stellen gegen die Ausgaben auf dem Gerät geprüft. Ausgeführt wurde nichts Scharfes. Die vollständige Liste mit Zeilenangaben steht jetzt in TASKS.md; hier die Kurzfassung, nach Schwere.

**Was dringend ist**

1. Stufe 2 friert bis zu drei Prozesse ein, nicht den größten Verursacher – `act_suspend` und `act_kill` haben `limit=3`, und es gibt keine Mindestmenge. Ein Videocall mit 500 KB im Fenster wird zusammen mit dem 29-MB-Download eingefroren. Mit `kill` würden drei Prozesse beendet.

2. `unblock` macht netguard nicht wieder scharf. Die Stufe fällt erst nach 300 s Cooldown; nach Stufe 3 plus `unblock` läuft der fortgesetzte Download bis zu fünf Minuten unbeobachtet weiter (bei 5 MB/s rund 1,4 GB). Mit Tageslimit ist netguard nach `unblock` sogar bis Mitternacht stumm.

3. Der automatische Wechsel auf den Diff-Modus ist ein Fehlentscheid aus der Historie: Er wurde in 919c7a1 eingeführt, weil nettop angeblich keine Deltas lieferte – das war der Parserfehler aus 268429c. Der Wechsel ist einbahnig, löst bei Loopback- oder AirDrop-Verkehr und nach zwei nettop-Timeouts fälschlich aus, und im Diff-Modus bekommt ein Prozess, der gerade einen Socket schließt, sein gesamtes bisheriges Volumen als „Delta“ zugeschrieben – und wird eingefroren statt des echten Verursachers. Empfehlung: Automatik streichen, nettop mit `-t wifi`/`wired` auf das gemessene Interface einschränken.

**Was mittelfristig behoben werden sollte**

4. Traffic zwischen zwei nettop-Fenstern wird nicht gezählt – im Normalbetrieb 1–2 %, nach einem Trip mehrere Sekunden, die im Fenster und im Tageszähler fehlen. Version 1.0 hatte das richtig.
5. Beim Tageswechsel wird die Stufe zurückgesetzt, das Fenster aber nicht: Ein Trip kurz vor Mitternacht feuert um 00:00 ein zweites Mal.
6. Die `ps comm`-Absicherung gegen gekürzte nettop-Namen läuft im Monitor nie (kein `snap` übergeben); die `NEVER_SUSPEND`-Liste vergleicht nur 15-Zeichen-Namen. Es fehlen unter anderem cloudd, bird, fileproviderd, softwareupdated, Dock, cfprefsd, runningboardd.
7. pf: Es wird nie geprüft, ob der Anchor im Haupt-Ruleset hängt (ohne `install-pf` oder nach einem macOS-Update blockt es still nichts), und die Blockregeln liegen in einer Datei, die beim Boot geladen wird. Token-Parsing und `-F states` sind in Ordnung. Sauberer wäre der Anchor `com.apple/000.netguard`, dann bleibt /etc/pf.conf unangetastet.
8. `lstart` als PID-Identität ist locale-abhängig; auf diesem Mac kein Problem, `LC_ALL=C` kostet eine Zeile.
9. Strg-C setzt eingefrorene Prozesse fort, die README behauptet das Gegenteil.
10. Bei einem USB-Hotspot schaltet Stufe 3 das WLAN ab, über das nichts läuft.

**Was in Ordnung ist**

zsh-Datei (`zsh -n` sauber, `NETGUARD_HOME`-Ermittlung, Argument-Arrays, Leerzeichen in `NETGUARD_SAY`), Python-Kompatibilität mit 3.9 und dem hier tatsächlich verwendeten 3.14, `wifi_device()`-Regex auf diesem Gerät, argparse in beiden Reihenfolgen, atomares state.json, Rotation, Signal-Handler, Standby-Erkennung, `cmd_top`, `shlex.quote` beim afplay-Aufruf, `pfctl -E`-Token. Einziger Namenskonflikt: `ng` ist das Angular-CLI-Kommando.

**Root**

Die sudo-Grenze ist nominell: Skript und Interpreter (python.org-Framework, admin-beschreibbar) liegen in Benutzerhand und laufen als root, mit `install-agent` dauerhaft. Auf dem eigenen Laptop vertretbar, gehört aber in die README. Logs im Repo-Ordner sind dagegen unkritisch.

**Noch offen**

Die Mitteilung unter sudo habe ich nicht gesehen – `sudo ./netguard.py testsound` scheiterte mit „command not found“ (falsches Verzeichnis oder fehlendes x-Bit). Einmal aus `netguard/` heraus mit dem vollen Python-Pfad ausführen, dann ist auch das geklärt.

Viele Grüße
Eddy
