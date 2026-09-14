# img-pipeline

Ein Skript: Bilder verkleinern/komprimieren, bevor sie einer Claude-Session
vorgelegt werden (Referenzbilder, Mockups, Karten-Artwork, Screenshots).
Grund: grosse Bilder erzeugen Traffic-Bursts, die netguard auffallen (siehe
`../netguard`).

Bewusst ohne jede Anbindung an einzelne Projekte/Repos (`world01`, `game04`,
...) - das Tool weiss nichts von ihnen und schreibt nichts in sie hinein.
Komprimierte Bilder danach bei Bedarf selbst ins jeweilige Repo kopieren.

## Einbinden

Einmalig in `~/.zshrc`:

```sh
source ~/Desktop/dev-tools/img-pipeline/shell/img-pipeline.zsh
```

## Nutzung

```sh
cd irgendein/ordner/mit/bildern
compressimgs                            # kompletten Ordner rekursiv verarbeiten
compressimgs --max-dim 1600 --quality 80
compressimgs karte_drache.png           # nur eine bestimmte Datei, keine Rekursion
compressimgs --redo                     # bestehende _compressed-Dateien neu erzeugen
compressimgs --replace                  # Originale direkt ersetzen, kein _compressed daneben
```

`compressimgs` bezieht sich immer auf das Verzeichnis, in dem du gerade
stehst (`$PWD`) - nicht auf den `dev-tools`-Ordner selbst. Es durchsucht
diesen Ordner **rekursiv inklusive aller Unterordner** nach `.jpg`/`.jpeg`/
`.png` und legt direkt daneben eine komprimierte Kopie mit `_compressed`
im Dateinamen an, z. B.:

```
concepts/diegetic_examples/karte_drache.png
concepts/diegetic_examples/karte_drache_compressed.png
```

Das gilt fuer jeden Unterordner in der Tiefe, egal wie er heisst. Ausnahmen
sind nur `.git`, `target`, `node_modules`, `__pycache__`, `.venv`, `venv`,
`.agent-check` - die werden beim Scan uebersprungen.

* **Originale werden nie angefasst.**
* Eine bereits vorhandene `_compressed`-Datei wird beim Scan nie als Quelle
  behandelt (kein Verschachteln von `_compressed_compressed`).
* Ist eine `_compressed`-Datei schon vorhanden und nicht aelter als das
  Original, wird sie beim erneuten Aufruf uebersprungen (Zeitstempel-
  Vergleich) - `compressimgs` mehrfach im selben Ordner laufen zu lassen
  ist also guenstig. `--redo` erzwingt eine Neuerzeugung.
* JPEG/PNG werden verkleinert (`--max-dim`, Default 2048px laengste Kante);
  PNG bleibt PNG (verlustfrei, behaelt Transparenz), JPEG wird zusaetzlich
  mit `--quality` (Default 82) neu komprimiert.
* `--force-jpeg` wandelt auch PNGs verlustbehaftet zu JPEG (kleiner, aber
  keine Transparenz mehr) - nur wenn du das wirklich willst.

## --replace: Originale direkt ersetzen

Standardmaessig legt `compressimgs` neben jedem Original eine `_compressed`-
Kopie an. Mit `--replace` passiert das nicht - stattdessen wird das
Original unter dem **exakt gleichen Namen** komprimiert ersetzt:

```sh
compressimgs --replace
```

**Unwiderruflich, keine Sicherheitskopie.** Das Original ist danach weg,
nur die komprimierte Version bleibt unter dem alten Namen liegen.

Damit ein zweiter Lauf im selben Ordner ein bereits ersetztes Bild nicht
nochmal (verlustbehaftet, mit sichtbarem Qualitaetsverlust) komprimiert,
merkt sich das Skript pro Datei ein xattr (ein Dateiattribut, keine
zusaetzliche Datei im Ordner) mit dem Zustand direkt nach dem Ersetzen.
Nur Bilder, die sich seitdem wirklich geaendert haben (neuer Zeitstempel
und/oder andere Groesse - z. B. weil du ein neues Original reinkopiert
hast), werden beim naechsten `--replace`-Lauf neu komprimiert. `--redo`
erzwingt trotzdem eine Neukomprimierung aller Treffer.

Sonderfall `--replace --force-jpeg` auf einer PNG-Datei: die Endung MUSS
sich zu `.jpg` aendern, dabei bleibt der Name nicht 1:1 gleich. Das alte
`.png` wird danach geloescht, damit nicht zwei Dateien uebrig bleiben.

Keine Abhaengigkeiten: nutzt ausschliesslich das in macOS eingebaute
`sips` (Komprimierung) und `xattr` (Merker fuer `--replace`).

## Bekannte Unsicherheit: --quality-Syntax

`sips -s formatOptions <Wert>` wird in verschiedenen Quellen unterschiedlich
dokumentiert - mal als Zahl 0-100, mal nur als Schluesselwort
(`low`/`normal`/`high`/`best`/`default`). Das Skript ist mit Default `82`
(numerisch) gebaut, aber **noch nicht auf einem echten Mac getestet worden**
(Logik in einer Cloud-Sandbox mit simuliertem `sips` durchgetestet, die
eigentliche Bildkomprimierung selbst nicht). Schlaegt die Zahl fehl, zeigt
das Skript den sips-Fehler klar an (keine stille Fehlproduktion) - dann
einfach `--quality high` statt einer Zahl probieren.

**Bevor du dich darauf verlaesst: einmal mit einem Testbild laufen lassen
und das Ergebnis pruefen.**
