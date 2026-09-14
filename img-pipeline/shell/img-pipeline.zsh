#!/usr/bin/env zsh
# ---------------------------------------------------------------------------
# img-pipeline - Bilder verkleinern/komprimieren, bevor sie einer Claude-
# Session vorgelegt werden.
#
# In ~/.zshrc einbinden:
#   source ~/Desktop/dev-tools/img-pipeline/shell/img-pipeline.zsh
#
# Danach, in JEDEM Ordner:
#   compressimgs                 # aktuellen Ordner rekursiv verarbeiten
#   compressimgs --max-dim 1600 --quality 80
#   compressimgs karte.png       # nur eine bestimmte Datei
#
# compressimgs ist eine zsh-Funktion, kein Programm - sie ruft im
# Hintergrund compress-for-claude auf, und zwar IMMER bezogen auf das
# Verzeichnis, in dem du gerade stehst ($PWD), nicht auf den Ordner
# dieses Skripts. Legt neben jedem .jpg/.jpeg/.png eine "_compressed"-
# Kopie an, rekursiv in allen Unterordnern. Originale werden nie
# angefasst, siehe compress-for-claude --help fuer Details.
# ---------------------------------------------------------------------------

if [[ -z ${IMG_PIPELINE_HOME:-} ]]; then
  _img_pipeline_src=${${(%):-%x}:A}
  if [[ -n $_img_pipeline_src && -f $_img_pipeline_src ]]; then
    IMG_PIPELINE_HOME=${_img_pipeline_src:h:h}
  else
    IMG_PIPELINE_HOME=$HOME/Desktop/dev-tools/img-pipeline
  fi
  unset _img_pipeline_src
fi
: "${IMG_PIPELINE_PY:=$(command -v python3 || echo /usr/bin/python3)}"
: "${IMG_PIPELINE_BIN:=$IMG_PIPELINE_HOME/compress-for-claude}"

compressimgs() {
  if [[ ! -f $IMG_PIPELINE_BIN ]]; then
    print -u2 "compressimgs: $IMG_PIPELINE_BIN nicht gefunden"
    return 1
  fi
  # Kein --root erzwungen: compress-for-claude nimmt dann selbst "."
  # relativ zu $PWD, also genau dem Ordner, in dem der Aufruf passiert.
  "$IMG_PIPELINE_PY" "$IMG_PIPELINE_BIN" "$@"
}
