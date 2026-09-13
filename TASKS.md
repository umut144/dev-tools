# TASKS

Offene Punkte im Monorepo. Erledigtes fliegt raus, nicht ab.

## netguard

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
      durch, die Stufen kommen im Kopf des Monitors an. Merke: `sudo
      netguard` scheitert mit *command not found*, weil sudo keine
      Shell-Funktionen kennt - die Funktion ruft sudo selbst auf.
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
