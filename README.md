# dev-tools

Monorepo fuer eigene kleine Werkzeuge.

| Ordner | Was es tut |
|---|---|
| [`netguard/`](netguard/) | Datenverbrauchs-Waechter fuer macOS: misst den Traffic, findet den Verursacher und eskaliert in drei Stufen bis zum WLAN-Aus |
| [`img-pipeline/`](img-pipeline/) | Bilder verkleinern/komprimieren, bevor sie einer Claude-Session vorgelegt werden - reduziert Traffic-Bursts durch grosse Referenzbilder/Mockups |

Jedes Werkzeug bringt seine eigene README und, wo sinnvoll, eine
Shell-Integration unter `<tool>/shell/` mit.
