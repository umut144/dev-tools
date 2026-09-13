# TASKS

Offene Punkte im Monorepo. Erledigtes fliegt raus, nicht ab.

## netguard

- [ ] **LaunchDaemon mit Warnstufen.** `install-agent` schreibt ein Plist mit
      genau einer Schwelle (`--burst-mb` + `--action`); die `--stage`-Liste
      wird nicht uebernommen. Erst noetig, wenn netguard dauerhaft im
      Hintergrund laufen soll statt im Terminal - aktuell bewusst nicht
      gebraucht.
- [ ] **nettop-Deltamodus auf echter Hardware bestaetigen.** Dass
      `nettop -d -L 2` im Logging-Modus wirklich Deltas liefert, ist bisher
      nur gegen synthetische Ausgaben geprueft. Die Plausibilitaetspruefung
      gegen das netstat-Delta faengt den Fehlerfall ab, ersetzt aber keine
      Messung: ein paar Tage `samples.jsonl` ansehen und pruefen, ob
      `unattributed_bytes` klein bleibt und `attribution_mode` auf `delta`
      steht.
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
