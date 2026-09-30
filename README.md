# dev-tools

Two small command-line tools for macOS, written in Python and zsh: a watchdog that monitors the machine's network usage and throttles in stages when something goes out of bounds, and a script that shrinks images before they are uploaded.

| Tool | Purpose | Usage |
|---|---|---|
| [`netguard/`](netguard/) | Measures traffic on the network interface, attributes it to processes, and escalates in three stages: warning sound, freeze the offending process, switch off Wi-Fi. | `netguard`, `netguard test`, `netguard status` |
| [`img-pipeline/`](img-pipeline/) | Recursively resizes and compresses JPEG/PNG using the `sips` tool built into macOS. | `compressimgs` |

Neither tool has dependencies beyond the Python standard library and macOS built-ins. Each has its own, more detailed README (currently in German).

## Motivation

`netguard` is meant for connections with a limited data allowance, such as a phone hotspot. A single background process that syncs or downloads can use up the allowance unnoticed. `netguard` shows which process it was and can stop it before it gets expensive.

`img-pipeline` is the counterpart: large reference images and screenshots uploaded to a Claude session cause exactly this kind of traffic spike. The script shrinks them beforehand.

## Installation

Requirements: macOS, zsh and Python 3. There are no packages to install. `netguard` uses `netstat`, `nettop`, `networksetup`, `afplay`, `osascript` and `launchctl`; `img-pipeline` uses `sips` and `xattr`.

```sh
git clone https://github.com/umut144/dev-tools.git ~/dev-tools
echo 'source ~/dev-tools/netguard/shell/netguard.zsh' >> ~/.zshrc
echo 'source ~/dev-tools/img-pipeline/shell/img-pipeline.zsh' >> ~/.zshrc
exec zsh
```

Keep the folder directly in your home directory, not under `Desktop`, `Documents` or `Downloads`: a LaunchDaemon may not read those folders without Full Disk Access (details in [`netguard/README.md`](netguard/README.md)).

`netguard` and `compressimgs` are zsh functions, not programs on the `PATH`. `sudo netguard` therefore does not work; the function calls `sudo` itself.

Note: the installation steps document how the tools work. Running them requires permission under the terms in [`LICENSE`](LICENSE).

## Usage

### netguard

Test mode only pretends to have traffic. It measures nothing real and performs no action, so it is the safe way in. With the zsh function, `netguard test` is enough; the call below is the equivalent via the script directly, with custom thresholds of 5, 10 and 20 MB per 10-second window and no sound:

```sh
python3 netguard/netguard.py --logdir /tmp/netguard-demo monitor \
  --interval 1 --window 10 \
  --stage 5:notify:none:1 --stage 10:suspend:none:1 --stage 20:wifi:none:1 \
  --simulate 4 --no-notify
```

Output (shortened; the tool's messages are in German; time, user and parent processes will differ on your machine):

```text
  SIMULATION - 4 MB/s vorgetaeuscht
  Es wird KEIN echter Traffic gemessen und KEINE Aktion
  ausgefuehrt. Toene und Meldungen sind echt.
netguard 1.2 - Interface en0, Sample 1s, Fenster 10s
  Stufe 1: ab 5 MB -> notify (ohne Ton)
  Stufe 2: ab 10 MB -> suspend (ohne Ton)
  Stufe 3: ab 20 MB -> wifi (ohne Ton)

!! [SIMULATION] STUFE 1/3 - 8.0 MB in 10s (Schwelle 5 MB)
   heute gesamt: 8.0 MB
       8.0 MB  simulierter-Download.6  user=...  uptime=00:02

   [SIMULATION] wuerde jetzt 'suspend' ausfuehren (simulierter-Download.6) - es passiert nichts.

!! [SIMULATION] STUFE 2/3 - 12.1 MB in 10s (Schwelle 10 MB)
   ...
!! [SIMULATION] STUFE 3/3 - 20.1 MB in 10s (Schwelle 20 MB)
   ...
[SIMULATION] hoechste Stufe erreicht - Test beendet.
```

In real operation:

| Command | Effect |
|---|---|
| `netguard` | Start monitoring (asks for the sudo password, Ctrl-C stops it) |
| `netguard top -v` | Snapshot: which process is pulling data right now? |
| `netguard status` | Lock status and today's volume |
| `netguard report` | The most recent logged incidents |
| `netguard unblock` | Release everything: resume processes, Wi-Fi back on |

The stages in detail, the protection list for system processes and running as a LaunchDaemon are described in [`netguard/README.md`](netguard/README.md).

Warning: in real operation `netguard` runs as root, freezes processes with `SIGSTOP` and switches off Wi-Fi in stage 3. Try `netguard test` first and read the notes on the sudo boundary in the netguard README.

### img-pipeline

```sh
cd ~/Pictures/references
compressimgs                     # all JPG/PNG, recursively
compressimgs --max-dim 1600 --quality 80
compressimgs dragon_card.png     # a single file
```

A shrunken copy is placed next to each original; the originals are left untouched:

```text
concepts/dragon_card.png
concepts/dragon_card_compressed.png
```

<!-- TODO: Paste the terminal output of `compressimgs` from a real Mac run (before/after sizes). Please create a small folder with 2-3 large PNG/JPG files, run `compressimgs` and paste the output here. -->

With `--replace`, originals are instead replaced irreversibly and without a backup.

## Configuration

Both tools read environment variables, which can be set in `~/.zshrc` before the `source` line. Defaults are in the respective `shell/*.zsh`.

| Variable | Meaning |
|---|---|
| `NETGUARD_HOME`, `NETGUARD_PY`, `NETGUARD_LOGDIR` | netguard folder, Python interpreter, log location |
| `NETGUARD_WINDOW`, `NETGUARD_INTERVAL` | Measurement window and sampling interval in seconds |
| `NETGUARD_S1_MB`, `NETGUARD_S2_MB`, `NETGUARD_S3_MB` | Thresholds of the three stages in MB per window |
| `NETGUARD_S1_SOUND` to `NETGUARD_S3_SOUND`, `NETGUARD_SAY` | Sounds and spoken announcement per stage |
| `NETGUARD_DAILY_MB` | Optional daily limit in MB, `0` = off |
| `IMG_PIPELINE_HOME`, `IMG_PIPELINE_PY`, `IMG_PIPELINE_BIN` | img-pipeline folder, interpreter and script path |

`compressimgs` accepts `--max-dim`, `--quality`, `--force-jpeg`, `--redo`, `--replace` and `--root`; `netguard.py monitor --help` lists the monitor's options.

netguard's logs live in `netguard/logs/` and are excluded via `.gitignore`: they contain the full command lines of the processes involved.

## Project structure

```text
netguard/netguard.py             Monitor and all subcommands (single file, standard library only)
netguard/shell/netguard.zsh      zsh function `netguard` / alias `ng`, thresholds and sounds
netguard/logs/                   Local logs and state (not versioned)
img-pipeline/compress-for-claude Image compression via sips
img-pipeline/shell/              zsh function `compressimgs`
TASKS.md                         Open items (German)
```

## Tests and quality

There are no automated tests and no CI. What exists:

* `netguard`'s simulation mode (`netguard test`), which plays through the stage logic without real traffic and without any action.
* A documented code review of `netguard`; the resulting items can be traced in the Git history and in `TASKS.md`.

According to its own README, `img-pipeline` has not yet been tested with real `sips` compression on a Mac; try it with test images before relying on it.

## Status and next steps

Personal tools in daily use, written for my own machine; they only run on macOS. Open items are in [`TASKS.md`](TASKS.md), among them a pf anchor that does not touch `/etc/pf.conf`, more targeted handling of iCloud, a billing period instead of a daily limit, and automatic resuming of frozen processes.

## License

Proprietary, all rights reserved. No permission to use, copy, modify or distribute is granted without prior written permission; see [`LICENSE`](LICENSE).
