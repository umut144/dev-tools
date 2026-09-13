# TASKS

Offene Punkte im Monorepo. Erledigtes fliegt raus, nicht ab.

## netguard

### Aus dem Review vom 2026-09-13 (nach Schwere)

- [ ] **Stufe 2/kill trifft bis zu drei Prozesse, ohne Mindestmenge.**
      `act_suspend`/`act_kill` haben `limit=3` (netguard.py:598, :613),
      `window_top()` nimmt jeden Prozess ab 1 Byte (:840). README sagt
      "der groesste Verursacher". Szenario: Safari 29 MB, zoom.us 500 KB,
      Spotify 60 KB -> alle drei bekommen SIGSTOP, der Call friert ein.
      Verschaerft durch `nsurlsessiond` in NEVER_SUSPEND: Systemupdate wird
      uebersprungen, zwei Unschuldige eingefroren, Update laeuft weiter.
      Fix: `limit=1` plus Mindestanteil am Fenster.
- [ ] **`unblock` macht netguard nicht wieder scharf.** `level` faellt nur
      nach `--cooldown` (:1151), `do_unblock()` laeuft in einem anderen
      Prozess. Nach Stufe 3 + `unblock` laeuft der fortgesetzte Download bis
      zu 300 s unbeobachtet (bei 5 MB/s: 1,4 GB). Mit Tageslimit bleibt
      `level` = 3 bis Mitternacht, kein Burst loest mehr aus (:1153).
      Fix: Monitor liest `unblocked_at` aus state.json je Zyklus und setzt
      `level = 0`, wenn es juenger als der letzte Trip ist. Dazu: bei
      Sprung 0 -> 3 die Prozess-Aktion der uebersprungenen Stufe mit
      ausfuehren, sonst steht der Verursacher nach `unblock` sofort wieder da.
- [ ] **Automatischen Wechsel auf Diff-Modus streichen.** Eingefuehrt in
      919c7a1 wegen "nettop liefert keine Deltas" - das war der Parserfehler
      aus 268429c. Der Wechsel ist einbahnig (nur :865/:892 setzen `mode`,
      nie zurueck), loest bei Loopback-/AirDrop-Verkehr falsch aus
      (`check_plausibility` vergleicht nettop ueber alle Interfaces mit
      netstat auf einem, :888) oder nach zwei nettop-Timeouts beim
      Aufwachen (:863). Im Diff-Modus wertet `diff_procs` (:335) einen
      sinkenden Zaehler als Neustart: Mail schliesst eine IMAP-Verbindung
      -> 295 MB "Delta", Mail wird eingefroren statt Safari.
      Fix: `diff` nur per `--attrib diff`; bei `ok=False` Zyklus ohne
      Zuordnung; nettop mit `-t wifi`/`wired` passend zum gemessenen
      Interface, dann stimmt die Plausibilitaetspruefung.
      Geprueft 26.6: Delta-Bloecke haben auch bei Ruhe Zeilen (18), der
      Ausloeser "leerer Block" faellt weg.
- [ ] **Traffic zwischen zwei nettop-Fenstern zaehlt nicht** (:911-920).
      Normal 1-2 %, nach einem Trip mehrere Sekunden (notify bis 10 s,
      lsof bis 8 x 8 s) - fehlt in Fenster und `day_bytes`. 1.0 hatte
      fortlaufende Zaehlerdifferenzen. Fix: Fenster/Tag aus `i1 - prev_i1`,
      Plausibilitaet aus `i1 - i0`. README-Rechnung "Stufe 1 erst ab
      9 MB/s uebersprungen" gilt nur ohne diese Pause.
- [ ] **Tageswechsel loest erneut aus.** `rollover_day()` setzt `level = 0`
      (:816), Fenster bleibt voll -> Stufe 3 feuert um 00:00 nochmal;
      4K-Stream ueber Mitternacht klingelt Stufe 1 zweimal. `level` nur
      zuruecksetzen, wenn er durch `daily_hit` entstand.
- [ ] **`ps comm`-Absicherung in `_targets()` laeuft im Monitor nie.**
      `execute_action(stage, top)` (:1015) uebergibt kein `snap`, verglichen
      wird nur der auf 15 Zeichen gekuerzte nettop-Name; `diskarbitrationd`
      (16) ist schon tot. Fehlende Eintraege: cloudd, bird, fileproviderd,
      sharingd, rapportd, softwareupdated, photolibraryd, Dock,
      ControlCenter, NotificationCenter, cfprefsd, runningboardd, tccd,
      wifid/airportd. Besser: vor dem Signal `ps -o uid=,comm= -p PID`
      und nur Prozesse des Konsolenbenutzers anhalten.
- [ ] **pf-Kette.** (a) `act_pf_block` prueft nicht, ob `anchor "netguard"`
      im Haupt-Ruleset haengt - ohne `install-pf` oder nach einem
      macOS-Update, das pf.conf zuruecksetzt, blockt es still nichts.
      (b) Blockregeln liegen in /etc/pf.anchors/netguard und werden beim
      Boot geladen: Absturz vor `unblock` -> en0 bleibt im Anchor gesperrt,
      sobald irgendwer pf einschaltet. Regeln per stdin laden
      (`pfctl -a … -f -`), Datei leer lassen. (c) Anchor haengt hinter
      `com.apple/*`, dessen `pass quick` gewinnt. Alternative fuer alles:
      Anchor `com.apple/000.netguard` - immer ausgewertet, vor
      250.ApplicationFirewall, pf.conf unangetastet. Token-Parsing und
      `-F states` sind in Ordnung.
- [ ] **`lstart` fuer die PID-Identitaet mit `LC_ALL=C` aufrufen** (:423).
      `%c` ist locale-abhaengig; en_US und C sind hier identisch, de_DE in
      einem anderen Terminal koennte abweichen -> `unblock` ueberspringt den
      Prozess, er bleibt eingefroren.
- [ ] **Strg-C setzt eingefrorene Prozesse fort** (`cleanup()`, :1048),
      README/zsh-Hilfe sagen "bleiben so bis unblock". Doku anpassen und
      beim Beenden ausgeben, was fortgesetzt wurde.
- [ ] **WLAN-Aktion bei USB-Hotspot.** `wifi_device()` liefert en0, der
      Traffic laeuft ueber `iPhone USB` (en5/en6): Stufe 3 schaltet das
      falsche Interface ab und meldet "gesperrt". Fallback auf `iface down`
      oder Warnung, wenn `iface != wifi_device()`. Regex passt auf diesem
      Mac (Port heisst "Wi-Fi").
- [ ] **LaunchDaemon und Shell nutzen verschiedene state.json**
      (/var/log/netguard vs. netguard/logs): `ng unblock` meldet "Nichts war
      gesperrt", waehrend der Daemon blockt. Erst relevant mit install-agent.
- [ ] **Root-Hinweis in die README.** netguard.py und der Interpreter
      (python.org-Framework 3.14, admin-beschreibbar) laufen per sudo bzw.
      als LaunchDaemon mit root - jedes Programm des Benutzers kann beides
      aendern. Logs: root folgt Symlinks in einem Benutzer-Verzeichnis
      (`_append`, `write_state`); `Logger` macht chmod 0700 + chown auf
      jedes existierende `--logdir`.
- [ ] **Offen vom Review:** Mitteilung unter sudo einmal pruefen
      (`sudo <python3> netguard.py testsound` aus netguard/); x-Bit auf
      netguard.py pruefen, sonst scheitern die `sudo ./netguard.py`-Beispiele
      der README.

### Aeltere Punkte

- [ ] **LaunchDaemon mit Warnstufen.** `install-agent` schreibt ein Plist mit
      genau einer Schwelle (`--burst-mb` + `--action`); die `--stage`-Liste
      wird nicht uebernommen. Erst noetig, wenn netguard dauerhaft im
      Hintergrund laufen soll statt im Terminal - aktuell bewusst nicht
      gebraucht.
- [x] ~~nettop-Ausgabe auf echter Hardware pruefen.~~ Erledigt: auf
      macOS 26.6 heisst die Kopfzeile `,bytes_in,bytes_out,` - erstes Feld
      leer - und Zeitstempel-Zeilen gibt es nicht, die wiederholte Kopfzeile
      ist die Sample-Grenze. Der Parser verwarf beides und lieferte deshalb
      gar keine Zuordnung. Behoben, Regressionstest gegen die echte Ausgabe.
- [x] ~~Liefert `nettop -d` echte Deltas?~~ Ja, auf macOS 26.6 bestaetigt:
      27,2 KB zugeordnet bei 30,6 KB Interface-Delta ueber 10 s, also rund
      89 % - kumulative Werte haetten hier hunderte MB ergeben. Der Rest
      sind ARP, mDNS und anderer Verkehr ohne Socket-Zuordnung. Richtwert
      fuer spaeter: bleibt `unattributed_bytes` bei nennenswertem Traffic
      unter etwa einem Viertel und steht `attribution_mode` auf `delta`,
      arbeitet die Zuordnung korrekt.
- [x] ~~zsh-Integration mit echtem zsh testen.~~ Laeuft: `netguard` und
      `netguard top -v` reichen sudo, `--logdir` und Subkommando korrekt
      durch, die Stufen kommen im Kopf des Monitors an; `zsh -n` sauber.
      Merke: `sudo netguard` scheitert mit *command not found*, weil sudo
      keine Shell-Funktionen kennt - die Funktion ruft sudo selbst auf.
      `ng` kollidiert mit dem Angular-CLI, falls das je installiert wird.
- [ ] **Schwellen nachjustieren.** 12/30/55 MB je 10 s sind eine
      Schaetzung, gemacht fuer den iPhone-Hotspot. Nach ein paar Tagen
      zeigt `netguard report`, was real ausgeloest hat: Stufe 1 bei ganz
      normalem Arbeiten heisst, die 12 MB sind zu scharf; `grep -c
      uebersprungen` ueber den Report heisst, der Messtakt von 2 s ist fuer
      die realen Downloadraten zu grob.
- [ ] **Auto-Resume.** Eingefrorene Prozesse bleiben eingefroren, bis
      `netguard unblock` kommt. Denkbar: nach N Sekunden Ruhe automatisch
      SIGCONT, mit Zaehler - beim dritten Mal bleibt der Prozess unten.
- [ ] **Abrechnungszeitraum.** Bisher nur ein Tageslimit. Fuer einen
      Monatstarif braeuchte es einen Monatszaehler mit konfigurierbarem
      Abrechnungstag.
