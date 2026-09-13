# TASKS

Offene Punkte im Monorepo. Erledigtes fliegt raus, nicht ab.

## netguard

Stand nach dem externen Review. Behobenes steht unten.

- [ ] **LaunchDaemon mit Warnstufen.** `install-agent` schreibt ein Plist mit
      genau einer Schwelle (`--burst-mb` + `--action`); die `--stage`-Liste
      wird nicht uebernommen. Erst noetig, wenn netguard dauerhaft im
      Hintergrund laufen soll statt im Terminal.
- [ ] **pf-Anchor ohne Eingriff in /etc/pf.conf.** Vorschlag aus dem Review:
      statt `anchor "netguard"` an pf.conf anzuhaengen, den verschachtelten
      Anchor `com.apple/000.netguard` benutzen - der haengt bereits im
      System-Ruleset. Dann ueberlebt der Kill-Switch auch ein macOS-Update,
      das pf.conf ersetzt. Muss auf dem Geraet geprueft werden, bevor es
      eingebaut wird; die aktuelle Fassung meldet immerhin, wenn der Anchor
      nicht haengt, und schaltet ersatzweise das Interface ab.
- [ ] **Mitteilung unter sudo pruefen.** Der Weg ueber
      `launchctl asuser <uid> sudo -u <user> osascript` ist nie beobachtet
      worden. Einmal `sudo python3 ~/Desktop/dev-tools/netguard/netguard.py
      testsound` ausfuehren (voller Pfad, sonst "command not found") und
      schauen, ob Ton und Mitteilung ankommen.
- [ ] **iCloud gezielt behandeln.** `cloudd` und `bird` stehen jetzt in
      `NEVER_SUSPEND`, weil ihr Einfrieren Finder und Dateianbieter haengen
      lassen kann. Damit kann Stufe 2 gegen eine ausser Kontrolle geratene
      iCloud-Synchronisation nichts ausrichten. Denkbar waere, stattdessen
      gezielt die Synchronisation zu pausieren, statt den Prozess anzuhalten.
- [ ] **Schwellen nachjustieren.** 12/30/55 MB je 10 s sind eine
      Schaetzung, gemacht fuer den iPhone-Hotspot. Nach ein paar Tagen
      zeigt `netguard report`, was real ausgeloest hat: Stufe 1 bei ganz
      normalem Arbeiten heisst, die 12 MB sind zu scharf; `grep -c
      uebersprungen` ueber den Report heisst, der Messtakt von 2 s ist fuer
      die realen Downloadraten zu grob.
- [ ] **Auto-Resume.** Eingefrorene Prozesse bleiben eingefroren, bis
      `netguard unblock` kommt oder netguard endet. Denkbar: nach N Sekunden
      Ruhe automatisch SIGCONT, mit Zaehler - beim dritten Mal bleibt der
      Prozess unten.
- [ ] **Abrechnungszeitraum.** Bisher nur ein Tageslimit. Fuer einen
      Monatstarif braeuchte es einen Monatszaehler mit konfigurierbarem
      Abrechnungstag.

### Erledigt

- [x] nettop-Ausgabe auf echter Hardware geprueft: Kopfzeile
      `,bytes_in,bytes_out,` mit leerem erstem Feld, keine Zeitstempel-
      Zeilen, die wiederholte Kopfzeile ist die Sample-Grenze. Parser
      entsprechend korrigiert (268429c).
- [x] `nettop -d` liefert echte Deltas: 27,2 KB zugeordnet bei 30,6 KB
      Interface-Delta ueber 10 s. Richtwert: bleibt `unattributed_bytes`
      bei nennenswertem Traffic unter etwa einem Viertel, arbeitet die
      Zuordnung korrekt.
- [x] zsh-Integration im Alltag bestaetigt. Merke: `sudo netguard` scheitert,
      weil sudo keine Shell-Funktionen kennt - die Funktion ruft sudo selbst.
- [x] Review-Punkte 1 bis 10 abgearbeitet (6bbdc18, 76ff107 und der
      Doku-Commit): Anteilsregel bei Stufe 2, unblock wird erkannt,
      Diff-Automatik entfernt, nettop auf den Interface-Typ eingeschraenkt,
      ps-Schnappschuss vor der Aktion, Schutzliste erweitert, LC_ALL=C,
      durchgehende Zaehlung, kein Doppel-Trip um Mitternacht, pf-Anchor
      wird geprueft, WLAN-Aktion weicht auf das gemessene Interface aus.
