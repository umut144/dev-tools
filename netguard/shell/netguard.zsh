#!/usr/bin/env zsh
# ---------------------------------------------------------------------------
# netguard - Datenverbrauchs-Waechter fuer macOS
#
# In ~/.zshrc einbinden:
#   source ~/Desktop/dev-tools/netguard/shell/netguard.zsh
#
# Danach:
#   netguard            # ueberwachen (fragt nach dem sudo-Passwort)
#
# netguard ist eine zsh-Funktion, kein Programm: 'sudo netguard' findet sie
# nicht ("command not found"). Die Funktion ruft sudo selbst auf.
#   netguard test       # Trockenlauf mit 4 MB/s, ohne echten Verbrauch
#   ng status           # laeuft gerade eine Sperre?
#   ng unblock          # alles wieder freigeben
# ---------------------------------------------------------------------------

# --- Pfade -----------------------------------------------------------------
# Ordner dieser Datei, zwei Ebenen hoch - mit hartem Fallback, falls die
# Datei anders eingebunden wird als per source.
if [[ -z ${NETGUARD_HOME:-} ]]; then
  _netguard_src=${${(%):-%x}:A}
  if [[ -n $_netguard_src && -f $_netguard_src ]]; then
    NETGUARD_HOME=${_netguard_src:h:h}
  else
    NETGUARD_HOME=$HOME/Desktop/dev-tools/netguard
  fi
  unset _netguard_src
fi
: "${NETGUARD_PY:=$(command -v python3 || echo /usr/bin/python3)}"
: "${NETGUARD_LOGDIR:=$NETGUARD_HOME/logs}"   # Logs liegen im Repo-Ordner
                                            # (Testlaeufe darunter in simulation/)

# --- feste Werte: hier aendern ---------------------------------------------
: ${NETGUARD_WINDOW:=10}        # Messfenster in Sekunden
: ${NETGUARD_INTERVAL:=2}       # Messtakt in Sekunden
: ${NETGUARD_S1_MB:=8}          # Stufe 1: nur Warnton
: ${NETGUARD_S2_MB:=16}         # Stufe 2: Verursacher einfrieren (SIGSTOP)
: ${NETGUARD_S3_MB:=32}         # Stufe 3: WLAN aus
: "${NETGUARD_S1_SOUND:=Ping}"
: "${NETGUARD_S2_SOUND:=Sosumi}"
: "${NETGUARD_S3_SOUND:=Submarine}"
: "${NETGUARD_SAY:=Achtung, Netzwerk wird getrennt}"
: ${NETGUARD_DAILY_MB:=0}       # optionales Tageslimit in MB, 0 = aus

_netguard_help() {
  cat <<'HELP'
netguard - Datenverbrauch ueberwachen und stufenweise bremsen

  netguard [watch]        Ueberwachung starten (sudo). Strg-C beendet.
  netguard test [MB/s]    Trockenlauf, Standard 4 MB/s. Kein echter Traffic,
                          keine echte Aktion - nur Toene und Ausgabe.
  netguard top [-v]       Momentaufnahme: wer zieht gerade Daten?
  netguard status         Sperrstatus und heutiges Volumen
  netguard unblock        Alles freigeben (Prozesse fortsetzen, WLAN an)
  netguard report         Die letzten Vorfaelle zusammenfassen
  netguard sound [Name]   Einen Systemsound probehoeren
  netguard daemon         Als LaunchDaemon installieren/aktualisieren -
                          laeuft ab jetzt bei jedem Systemstart im
                          Hintergrund, auch ohne offenes Terminal
  netguard daemon-status  Laeuft der Daemon gerade?
  netguard daemon-log     Live mitlesen, wie bei 'netguard' im Terminal
  netguard daemon-errors  Fehlerausgabe des Daemons (Abstuerze, Tracebacks)
  netguard daemon-off     Daemon stoppen (bleibt auch nach einem Neustart
                          aus, bis 'netguard daemon' erneut kommt)
  netguard daemon-uninstall  Daemon komplett entfernen (Plist geloescht)
  netguard help           Diese Hilfe

netguard ruft sudo selbst auf. 'sudo netguard ...' schlaegt fehl, weil sudo
Shell-Funktionen nicht kennt.

Laeuft der Daemon, ist ein zusaetzliches 'netguard watch' im Terminal
unnoetig (Doppelmessung) - status/unblock/report/test funktionieren aber
unveraendert nebenher, sie teilen sich dieselben Log-/State-Dateien.

Stufen (in shell/netguard.zsh aenderbar):
HELP
  print "  1. ab ${NETGUARD_S1_MB} MB / ${NETGUARD_WINDOW}s -> Warnton (${NETGUARD_S1_SOUND})"
  print "  2. ab ${NETGUARD_S2_MB} MB / ${NETGUARD_WINDOW}s -> Verursacher einfrieren (${NETGUARD_S2_SOUND})"
  print "  3. ab ${NETGUARD_S3_MB} MB / ${NETGUARD_WINDOW}s -> WLAN aus (${NETGUARD_S3_SOUND})"
  print ""
  print "Ein abgeschaltetes Netz bleibt aus, bis 'netguard unblock' kommt."
  print "Eingefrorene Prozesse laufen weiter, sobald netguard beendet wird"
  print "(Strg-C, Terminal zu, 'netguard daemon-off') oder du 'netguard"
  print "unblock' aufrufst."
}

netguard() {
  local script="$NETGUARD_HOME/netguard.py"
  if [[ ! -f $script ]]; then
    print -u2 "netguard: $script nicht gefunden. NETGUARD_HOME passend setzen."
    return 1
  fi

  local -a stages=(
    --stage "${NETGUARD_S1_MB}:notify:${NETGUARD_S1_SOUND}:1"
    --stage "${NETGUARD_S2_MB}:suspend:${NETGUARD_S2_SOUND}:3"
    --stage "${NETGUARD_S3_MB}:wifi:${NETGUARD_S3_SOUND}:5:${NETGUARD_SAY}"
  )
  local -a base=(
    --interval "$NETGUARD_INTERVAL"
    --window   "$NETGUARD_WINDOW"
    --daily-mb "$NETGUARD_DAILY_MB"
    "${stages[@]}"
  )

  local cmd=${1:-watch}
  (( $# > 0 )) && shift

  case $cmd in
    watch|start|"")
      print "netguard: ${NETGUARD_S1_MB}/${NETGUARD_S2_MB}/${NETGUARD_S3_MB} MB je ${NETGUARD_WINDOW}s - Strg-C beendet."
      sudo "$NETGUARD_PY" "$script" --logdir "$NETGUARD_LOGDIR" monitor \
           "${base[@]}" -v "$@"
      ;;
    test|sim)
      local rate=4
      if [[ -n $1 && $1 == [0-9]* ]]; then rate=$1; shift; fi
      "$NETGUARD_PY" "$script" --logdir "$NETGUARD_LOGDIR" monitor \
           "${base[@]}" --interval 1 --simulate "$rate" -v "$@"
      ;;
    sound)
      "$NETGUARD_PY" "$script" testsound --sound "${1:-$NETGUARD_S2_SOUND}"
      ;;
    daemon|daemon-install)
      print "netguard: LaunchDaemon installieren/aktualisieren (${NETGUARD_S1_MB}/${NETGUARD_S2_MB}/${NETGUARD_S3_MB} MB je ${NETGUARD_WINDOW}s) - startet ab sofort und ab jedem Systemstart im Hintergrund."
      sudo "$NETGUARD_PY" "$script" --logdir "$NETGUARD_LOGDIR" install-agent \
           --yes --interval "$NETGUARD_INTERVAL" --window "$NETGUARD_WINDOW" \
           --daily-mb "$NETGUARD_DAILY_MB" "${stages[@]}"
      ;;
    daemon-status)
      sudo /bin/launchctl print system/local.netguard 2>&1 | head -25
      ;;
    daemon-log|daemon-tail)
      print "Live-Ausgabe des Daemons (wie 'netguard' im Terminal) - Strg-C beendet nur das Mitlesen, nicht den Daemon:"
      tail -n 20 -f "$NETGUARD_LOGDIR/netguard.out.log"
      ;;
    daemon-errors)
      tail -n 50 "$NETGUARD_LOGDIR/netguard.err.log"
      ;;
    daemon-off|daemon-stop)
      # bootout allein reicht nicht: das Plist liegt weiter in
      # /Library/LaunchDaemons, und launchd laedt bei jedem Boot automatisch
      # alles dort - ohne "disable" waere der Daemon nach einem Neustart
      # wieder da. disable ist die Variante, die das ueberlebt.
      sudo /bin/launchctl bootout system /Library/LaunchDaemons/local.netguard.plist 2>&1
      sudo /bin/launchctl disable system/local.netguard 2>&1
      print "LaunchDaemon gestoppt - bleibt auch nach einem Neustart aus. 'netguard daemon' aktiviert und startet ihn wieder."
      ;;
    daemon-uninstall)
      sudo /bin/launchctl bootout system /Library/LaunchDaemons/local.netguard.plist 2>&1
      sudo /bin/launchctl disable system/local.netguard 2>&1
      sudo /bin/rm -f /Library/LaunchDaemons/local.netguard.plist
      print "LaunchDaemon komplett entfernt (Plist geloescht). 'netguard daemon' legt ihn bei Bedarf neu an."
      ;;
    help|-h|--help)
      _netguard_help
      ;;
    *)
      sudo "$NETGUARD_PY" "$script" --logdir "$NETGUARD_LOGDIR" "$cmd" "$@"
      ;;
  esac
}

alias ng=netguard
