# img-pipeline

Ein Skript: Bilder verkleinern/komprimieren, bevor sie einer Claude-Session
vorgelegt werden (Referenzbilder, Mockups, Karten-Artwork). Grund: grosse
Bilder erzeugen Traffic-Bursts, die netguard auffallen (siehe `../netguard`).

## Nutzung

```sh
./compress-for-claude karte_drache.png
./compress-for-claude --max-dim 1600 --quality 80 referenzen/*.jpg
./compress-for-claude --out-dir ~/Desktop/game04/fuer-claude *.png
```

Ohne `--out-dir` landen die Kopien in einem `compressed/`-Unterordner neben
dem ersten Bild. **Originale werden nie angefasst.**

* PNG bleibt PNG (verlustfrei, nur verkleinert) - behaelt Transparenz.
* JPEG/HEIC/TIFF/BMP/GIF werden verkleinert und mit `--quality` neu
  komprimiert (als JPEG ausgegeben).
* `--force-jpeg` wandelt auch PNGs verlustbehaftet zu JPEG (kleiner, aber
  keine Transparenz mehr) - nur wenn du das wirklich willst.

Keine Abhaengigkeiten: nutzt ausschliesslich das in macOS eingebaute `sips`.

## Bekannte Unsicherheit: --quality-Syntax

`sips -s formatOptions <Wert>` wird in verschiedenen Quellen unterschiedlich
dokumentiert - mal als Zahl 0-100, mal nur als Schluesselwort
(`low`/`normal`/`high`/`best`/`default`). Das Skript ist mit Default `82`
(numerisch) gebaut, aber **noch nicht auf einem echten Mac getestet worden**
(entstanden in einer Cloud-Sandbox ohne `sips`). Schlaegt die Zahl fehl,
zeigt das Skript den sips-Fehler klar an (keine stille Fehlproduktion) -
dann einfach `--quality high` statt einer Zahl probieren.

**Bevor du dich darauf verlaesst: einmal mit einem Testbild laufen lassen
und das Ergebnis pruefen.**
