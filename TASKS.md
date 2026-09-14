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

- [x] **SIGHUP abgefangen.** Bisher hoerte netguard nur auf SIGTERM/
      SIGINT, um angehaltene Prozesse beim Beenden wieder freizugeben.
      Schliesst man aber das Terminal-Fenster/Tab, in dem netguard
      laeuft, schicken Terminal.app/iTerm dabei ueblicherweise SIGHUP an
      die Prozessgruppe - ohne eigenen Handler beendet Python den
      Prozess sofort und OHNE cleanup(), eingefrorene (SIGSTOP)
      Prozesse waeren dann dauerhaft haengen geblieben, bis sie manuell
      per `kill -CONT` befreit werden. Anlass: nach einem Stufe-2-Trip
      gegen PasswordBreachAgent stand in state.json weiterhin ein
      `suspended`-Eintrag ohne `unblocked_at`, obwohl `ng unblock` nicht
      gelaufen war - ob der Prozess auf dem Geraet tatsaechlich noch
      angehalten war, liess sich von hier aus nicht pruefen (kein
      Zugriff auf die echte Mac-Prozessliste), aber die Luecke im Code
      war unabhaengig davon real. SIGHUP ist jetzt Teil der
      abgefangenen Signale.

- [x] nettop-Ausgabe auf echter Hardware geprueft: Kopfzeile
      `,bytes_in,bytes_out,` mit leerem erstem Feld, keine Zeitstempel-
      Zeilen, die wiederholte Kopfzeile ist die Sample-Grenze. Parser
      entsprechend korrigiert (268429c).
- [x] `nettop -d` liefert echte Deltas: 27,2 KB zugeordnet bei 30,6 KB
      Interface-Delta ueber 10 s. Richtwert: bleibt `unattributed_bytes`
      bei nennenswertem Traffic unter etwa einem Viertel, arbeitet die
      Zuordnung korrekt.
- [x] Alarm unter sudo bestaetigt: der Weg ueber
      `launchctl asuser <uid> sudo -u <user>` liefert Ton und Mitteilung -
      geprueft mit `testsound` und ueber alle drei Stufen im Testlauf. Dazu
      behoben, dass die Tonkette vorzeitig abbrach, die Mitteilung ihren
      eigenen Ton darueberlegte und die Ansage in die Toene hineinsprach
      (f358906).
- [x] zsh-Integration im Alltag bestaetigt. Merke: `sudo netguard` scheitert,
      weil sudo keine Shell-Funktionen kennt - die Funktion ruft sudo selbst.
- [x] Review-Punkte 1 bis 10 abgearbeitet (6bbdc18, 76ff107 und der
      Doku-Commit): Anteilsregel bei Stufe 2, unblock wird erkannt,
      Diff-Automatik entfernt, nettop auf den Interface-Typ eingeschraenkt,
      ps-Schnappschuss vor der Aktion, Schutzliste erweitert, LC_ALL=C,
      durchgehende Zaehlung, kein Doppel-Trip um Mitternacht, pf-Anchor
      wird geprueft, WLAN-Aktion weicht auf das gemessene Interface aus.
