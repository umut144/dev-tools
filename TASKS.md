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
- [ ] **Liefert `nettop -d` echte Deltas?** Weiterhin offen: der zweite
      Block koennte auch nur wieder kumulative Werte enthalten. `netguard
      top -v` beantwortet das - steht unter "davon zugeordnet" eine Zahl in
      der Groessenordnung des Interface-Totals, stimmt es; sind es hunderte
      MB, wird `-d` ignoriert und netguard schaltet selbst auf kumulatives
      Diffen um. Faellt es dauerhaft auf `diff` zurueck, lohnt sich die
      Ueberlegung, den Diff-Modus gleich zum Standard zu machen.
- [ ] **zsh-Integration mit echtem zsh testen.** Die Datei wurde nur
      strukturell geprueft (Klammern, Quotes, case/esac), nie ausgefuehrt -
      in der Entwicklungsumgebung war kein zsh verfuegbar.
- [ ] **Schwellen nachjustieren.** 12/24/36 MB je 10 s sind eine Schaetzung.
      Nach ein paar Tagen zeigt `netguard report`, was real ausgeloest hat
      und ob Stufe 1 zu oft oder zu selten kommt.
- [ ] **Auto-Resume.** Eingefrorene Prozesse bleiben eingefroren, bis
      `netguard unblock` kommt. Denkbar: nach N Sekunden Ruhe automatisch
      SIGCONT, mit Zaehler - beim dritten Mal bleibt der Prozess unten.
- [ ] **Abrechnungszeitraum.** Bisher nur ein Tageslimit. Fuer einen
      Monatstarif braeuchte es einen Monatszaehler mit konfigurierbarem
      Abrechnungstag.
