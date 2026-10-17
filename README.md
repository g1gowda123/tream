# tream

Fast, frictionless torrent streaming and download tool from the terminal.

Inspired by the simplicity of `ani-cli`, **tream** lets you search indexers concurrently, select a result with an interactive picker, and stream it immediately in MPV or VLC—or download it to disk with live progress. TorrServer lifecycle, memory buffer sizing, series/episode playlist grouping, resumable downloads, and playback resume are all handled under the hood. When the player closes you get a follow-up menu (resume, next episode, pick another result, new search) so you never re-search from scratch.

---

## Installation

### Prerequisites

You need **MPV** (or **VLC**) and **TorrServer** on your system.

- **Arch Linux / CachyOS**:
  ```bash
  sudo pacman -S mpv
  # TorrServer is in the AUR (torrserver-bin):
  paru -S torrserver-bin
  # (or: yay -S torrserver-bin)
  ```
- **Ubuntu / Debian**:
  ```bash
  sudo apt install mpv
  # Download TorrServer release from https://github.com/YouROK/TorrServer/releases
  ```
- **macOS (Homebrew)**:
  ```bash
  brew install mpv
  # Download TorrServer Darwin binary from https://github.com/YouROK/TorrServer/releases
  ```
- **Windows 10/11 (PowerShell)**:
  ```powershell
  winget install mpv.net
  # Download TorrServer Windows executable and place in PATH
  ```
- **Android (Termux)**:
  ```bash
  pkg install python mpv git
  ```

Verify everything with the built-in diagnostics:

```bash
tream --doctor           # checks TorrServer, player, config, providers
tream --doctor --network # also tests provider connectivity
```

---

### One-Line Install per Platform

**Linux & macOS (via pipx - recommended)**:
```bash
pipx install git+https://github.com/g1gowda123/tream.git
```

**Windows (PowerShell)**:
```powershell
pipx install git+https://github.com/g1gowda123/tream.git
```

**Android (Termux)**:
```bash
pip install git+https://github.com/g1gowda123/tream.git
```

**From Local Clone**:
```bash
git clone https://github.com/g1gowda123/tream.git
cd tream
pipx install .
```

---

## Quickstart & Usage

The command name is `tream`. Subcommands and modifiers are accessible as clean root flags.

### 1. Stream Movies & Releases
Search across enabled indexers (YTS, Apibay, Nyaa, 1337x, Torznab) and pick interactively:
```bash
tream bladerunner
```

Run `tream` with no arguments and it will prompt for a query:
```bash
tream
# ? Search: severance
```

The picker supports paging (`[>> Next Page]`), **resolution filtering**, and **sorting** (seeders, size, best resolution, title) without leaving the prompt.

### 2. Stream Series & Anime with Episode Grouping (`-s`)
Automatically detect seasons and episodes (e.g. `S01E01`, `1x01`, `Ep 01`, `Show - 01`), pick your episode, and auto-advance playback across the season. Episodes you started before show a `(resume 12:34)` marker:
```bash
tream -s jujutsu kaisen
tream -s "the bear" --buffer 1G
```

Torrents with multiple seasons show a single episode list (with `── Season N ──` separators) so you can jump straight to any episode; single-season torrents with one episode skip the picker entirely.

Skip the prompts with explicit season/episode numbers:
```bash
tream -s "severance" --season 2 --episode 3
```

### 3. Playback Session & Fast Start
When the player closes, tream does not exit — an interactive follow-up menu appears instead:

- **Resume from 07:12** — continue where you stopped
- **Next episode: S01E04** — jump straight to the next one (series)
- **Pick another result** — back to the same search results, no new search
- **New search** — search for something else
- **Quit tream**

TorrServer keeps running between playbacks and the torrent stays loaded, so replaying, grabbing the next episode, or switching results starts almost immediately. Add `--once` to exit right after playback ends (handy for scripts).

tream also trims time-to-first-frame: torrent metadata is polled immediately (no fixed startup sleep), the `playback.preload_cache` default is a low 10 seconds, and in series mode tream starts fetching the first episode in the background while you are still picking.

### 4. Download Torrents (`-d`)
Search, choose a result, and download files directly to a folder with a live progress bar showing transferred bytes, rate, and ETA:
```bash
tream -d bladerunner ~/Downloads
tream -d "ubuntu 24.04" ./isos
tream -d "dune part two" -o ~/Movies      # explicit output folder
```

Multi-file torrents show a file picker (all files pre-selected, `space` toggles, `a` selects all). Other download niceties:

- **Resumable downloads**: interrupted files are kept as `<name>.part` and automatically continue (via HTTP Range requests) the next time you run the same command.
- **Skip completed files**: files whose size already matches are skipped.
- `-a/--all-files` downloads everything without prompting (great for scripting).
- `--overwrite` forces a fresh download even when files exist.
- The last positional argument is only treated as a folder when it looks like a path, so `tream -d "dune part two"` searches the full title instead of treating `two` as a directory.

### 5. Watch History & Resume (`-h`)
View your recent streams, timestamps, and playback positions, and resume with a single keypress:
```bash
tream -h            # interactive picker
tream -h --clear    # wipe watch history
tream -h --json     # machine-readable history
```

### 6. Direct Magnets & Infohashes (`--magnet`)
Stream or download a magnet link (or bare 40-char hex / 32-char base32 infohash) without searching. A lone magnet/infohash argument is auto-detected too:
```bash
tream --magnet "magnet:?xt=urn:btih:..." "Optional Title"
tream e36780c888d30e52516fa8a4f02fa5ac143cebb2
tream -d --magnet "magnet:?xt=urn:btih:..." ~/Downloads
tream -s --magnet "magnet:?xt=urn:btih:..." --season 1 --episode 5
```

### 7. Non-Interactive & Scripting
```bash
tream --list "blade runner"                 # print a ranked results table
tream --list "dune" -P yts --min-seeders 5  # only YTS, at least 5 seeders
tream --pick 1 "big buck bunny"             # stream the top-ranked result
tream -d --pick 2 "ubuntu" ./isos           # download the 2nd result, no prompts
tream --json "blade runner" | jq '.[0].magnet'
tream --json "blade runner" -P yts -P apibay
```

`--pick N` selects the Nth row printed by `--list` (1-based), making the whole flow usable from scripts and aliases. When stdin is not a terminal, tream never hangs on a prompt—it tells you to use `--pick`/`--list` instead.

### 8. Self-Update (`-U`)
Detects your installation method (git, pipx, or pip) and updates to the latest version:
```bash
tream -U
```

### 9. Environment Diagnostics (`--doctor`)
Runs a full environment checklist: Python version, config validity, writable directories, TorrServer binary and startup, media player, enabled providers (with `--network`, live connectivity), and subtitles:
```bash
tream --doctor
tream --doctor --network
```

### 10. Buffer Size Override (`--buffer` / `-b`)
Configure memory cache size passed to both TorrServer and the player demuxer:
```bash
tream "dune 2" --buffer 512M
tream -s severance -b 1G
```

### 11. Player Selection (`--player` / `-p`)
Override player for a run:
```bash
tream avatar --player vlc
tream avatar --player mpv
```

### 12. Configuration Management (`tream config`)
```bash
tream config show              # effective config table (env overrides highlighted)
tream config show --json       # effective config as JSON
tream config get playback.buffer
tream config set playback.buffer 512M   # validated before writing
tream config set search.providers "[yts, apibay, nyaa]"
tream config path              # where config, history, resume and logs live
tream config validate          # syntax + schema check
tream config edit
tream config reset             # restore defaults (asks first)
```

---

## Option Reference

| Flag | Description |
| --- | --- |
| `-s`, `--series` | Series mode: group episodes, episode picker, season auto-advance |
| `-d`, `--download` | Download instead of streaming |
| `-o`, `--output DIR` | Download folder (with `-d`) |
| `-a`, `--all-files` | With `-d`: download every file without prompting |
| `--overwrite` | With `-d`: re-download files even if they exist |
| `-h`, `--history` | Watch-history picker / resume |
| `--clear` | With `-h`: clear watch history |
| `-U`, `--update` | Self-update |
| `--doctor` | Environment diagnostics |
| `--network` | With `--doctor`: test provider connectivity |
| `-l`, `--list` | Print ranked results as a table |
| `--pick N` | Select the Nth ranked result non-interactively |
| `--once` | Exit after playback instead of showing the follow-up menu |
| `--magnet URI` | Stream/download a magnet or infohash directly |
| `--season N` | With `-s`: play this season without prompting |
| `--episode N` | With `-s`: play this episode without prompting |
| `-P`, `--provider NAME` | Restrict providers for this run (repeatable/comma-separated) |
| `--min-seeders N` | Hide results below N seeders |
| `-b`, `--buffer SIZE` | Buffer override (`256M`, `512M`, `1G`, …) |
| `-p`, `--player NAME` | Player override (`mpv` or `vlc`) |
| `--json` | JSON output for search results / history |
| `-v`, `--verbose` | Write debug logs to the cache directory |
| `-V`, `--version` | Print version |
| `--help` | Show help |

Options may appear before or after the query, e.g. `tream "dune 2" --buffer 1G`.

### Exit Codes

| Code | Meaning |
| --- | --- |
| `0` | Success (or the user cancelled cleanly) |
| `1` | No results, invalid input, or a failure (metadata timeout, download errors, …) |
| `130` | Interrupted with Ctrl+C |

---

## Configuration

Configuration is stored in standard cross-platform paths:

- **Linux / Termux**: `~/.config/tream/config.toml`
- **macOS**: `~/Library/Application Support/tream/config.toml`
- **Windows**: `%APPDATA%\tream\config.toml`

Default configuration:

```toml
[general]
player = "mpv"
torrserver_port = 8090
torrserver_binary = "torrserver"

[playback]
buffer = "256M"
preload_cache = 10
connections_limit = 800
enable_dht = true
prewarm = true

[search]
providers = ["yts", "apibay", "nyaa"]
max_results = 20
fuzzy = true
min_seeders = 0

[subtitles]
enabled = false
languages = ["en"]

[series]
auto_group = true
resume = true

[torznab]
url = ""
api_key = ""
```

### Environment Variable Overrides

Environment variables take highest precedence (`tream config show` marks them):
- `TREAM_BUFFER`: override default buffer size (e.g. `512M`, `1G`)
- `TREAM_PLAYER`: override default player (`mpv` or `vlc`)
- `TREAM_PORT`: override TorrServer HTTP port
- `TREAM_TORRSERVER_BIN`: custom TorrServer executable path

---

## Troubleshooting

### 0. Run `tream --doctor` first
It checks your Python, config, directories, TorrServer binary/startup, media player, and providers, and tells you exactly what to fix.

### 1. `TorrServer binary not found`
**Issue**: tream could not locate `torrserver` in `PATH` or standard locations.

**Solution**:
1. Download the pre-built binary for your platform from [TorrServer Releases](https://github.com/YouROK/TorrServer/releases).
2. Move it to a folder in your `PATH` (such as `/usr/local/bin` on Linux/macOS, or Windows PATH), and ensure execute permissions (`chmod +x torrserver`).
3. Alternatively, point tream directly to your binary location:
   ```bash
   tream config set general.torrserver_binary /path/to/torrserver
   ```
   or via environment variable:
   ```bash
   export TREAM_TORRSERVER_BIN=/path/to/torrserver
   ```

### 2. `No media player found`
**Issue**: Neither MPV nor VLC was detected in your PATH.

**Solution**:
- Install MPV (recommended) or VLC:
  - Debian/Ubuntu: `sudo apt install mpv`
  - Arch Linux: `sudo pacman -S mpv`
  - macOS: `brew install mpv`
  - Windows: `winget install mpv.net`
- If using an unusual installation directory:
  ```bash
  tream config set general.player /path/to/mpv
  ```

### 3. Port Conflict on 8090
If port 8090 is in use by another service on your machine:
```bash
tream config set general.torrserver_port 8095
# Or via environment variable:
export TREAM_PORT=8095
```

### 4. `metadata timeout; torrent has no active seeders`
Nothing is wrong with your setup—the chosen torrent simply has no reachable peers. Pick another result (`tream --list` shows seeder counts) or raise the seeder floor: `tream --min-seeders 10 "query"`.

### 5. A download was interrupted
Just re-run the exact same download command. tream keeps partial files as `.part` and resumes with an HTTP Range request; files that already finished are skipped.

### 6. Playback takes a long time to start
Time-to-first-frame is mostly peer speed, but you can nudge it:

```bash
tream "query" --min-seeders 10          # avoid poorly-seeded torrents
tream config set playback.preload_cache 5  # start serving sooner (default 10s)
tream config set playback.enable_dht true  # find more peers (default on)
```

`playback.prewarm` (default `true`) already starts fetching the first episode while you pick in `-s` mode.

---

## License

MIT License. Copyright (c) 2026 g1gowda123.
