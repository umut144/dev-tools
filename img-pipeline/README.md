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

Keine Abhaengigkeiten: nutzt ausschliesslich das in macOS eingebaute `sips`.

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
