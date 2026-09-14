# TASKS

Offene Punkte im Monorepo. Erledigtes fliegt raus, nicht ab.

## netguard

Stand nach dem externen Review. Behobenes steht unten.

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
- [ ] **Schwellen nachjustieren.** 10/26/47 MB je 10 s (zuvor 12/30/55,
      davor 12/24/36) - bewusst eher zu eng als zu locker eingestellt. Nach
      ein paar Tagen zeigt `netguard report`, was real ausgeloest hat: Stufe
      1 bei ganz normalem Arbeiten heisst, 10 MB ist zu scharf; `grep -c
      uebersprungen` ueber den Report heisst, der Messtakt von 2 s ist fuer
      die realen Downloadraten zu grob. Bei Dauerbetrieb ueber den Daemon
      zusaetzlich `daemon-status`/die Logdateien pruefen statt nur
      `netguard report` aus einer einzelnen Terminal-Session.
- [ ] **Auto-Resume.** Eingefrorene Prozesse bleiben eingefroren, bis
      `netguard unblock` kommt oder netguard endet. Denkbar: nach N Sekunden
      Ruhe automatisch SIGCONT, mit Zaehler - beim dritten Mal bleibt der
      Prozess unten.
- [ ] **Abrechnungszeitraum.** Bisher nur ein Tageslimit. Fuer einen
      Monatstarif braeuchte es einen Monatszaehler mit konfigurierbarem
      Abrechnungstag.

- [ ] **Daemon scheitert aktuell an TCC/Full Disk Access.** Repo liegt
      unter ~/Desktop - ein LaunchDaemon (root, ohne Terminal-Kontext)
      darf da seit macOS 10.15.4 ohne explizite Freigabe nicht lesen
      ("Operation not permitted", siehe netguard.err.log). Empfohlener
      Fix: Repo nach ~/dev-tools verschieben (ausserhalb Desktop/
      Dokumente/Downloads), .zshrc-Pfad anpassen, `netguard daemon`
      neu ausfuehren. Alternativ Full Disk Access fuer den konkreten
      Python-Interpreter erteilen (breiter als noetig). Braucht eine
      manuelle Aktion des Users - laesst sich nicht per Code loesen.
      Details in README ("Bekannte Stolperfalle").

### Erledigt

- [x] **daemon-off war nicht dauerhaft.** `launchctl bootout` stoppt nur
      die laufende Instanz - das Plist bleibt in /Library/LaunchDaemons
      liegen, und launchd laedt bei jedem Boot automatisch alles dort.
      Ohne `launchctl disable` waere ein per `daemon-off` gestoppter
      Daemon nach dem naechsten Neustart einfach wieder da gewesen -
      genau das Gegenteil von dem, was die Meldung versprach. Jetzt
      setzt `daemon-off` zusaetzlich `disable` (persistent), und
      `netguard daemon` setzt vor dem Neustart wieder `enable`, falls
      zuvor disabled wurde.

- [x] **LaunchDaemon mit allen drei Warnstufen.** `install-agent` nimmt
      jetzt wie `monitor` beliebig viele `--stage` an (Fallback auf die
      alte Einzelstufe `--burst-mb`/`--action` bleibt, falls nichts
      angegeben ist). Dazu `netguard daemon` / `daemon-status` /
      `daemon-off` in der zsh-Funktion, die automatisch die aktuellen
      NETGUARD_S1/2/3_MB-Werte uebernehmen. Nebenbei behoben: launchd
      startet den Daemon ohne `sudo`, also ohne `SUDO_UID` - der
      Besitz-Rueckfall in `_chown_to_invoker()` griff dort ins Leere
      und das Log-Verzeichnis waere dauerhaft root:root/0700 geblieben,
      selbst nach dem Login. Jetzt Fallback auf den grafisch
      eingeloggten Console-User (`console_user()`, dieselbe Funktion,
      die schon fuer Sound/Mitteilungen genutzt wird), und
      `write_state()` zieht zusaetzlich das Verzeichnis selbst nach,
      nicht nur die einzelne Datei.

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
