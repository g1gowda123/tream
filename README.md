# tream

Fast, frictionless torrent streaming and download tool from the terminal.

Inspired by the simplicity of `ani-cli`, **tream** lets you search indexers concurrently, select a result with an interactive picker, and stream it immediately in MPV or VLC—or download it to disk with live progress. TorrServer lifecycle, memory buffer sizing, and series/episode playlist grouping are all handled under the hood.

---

## Installation

### Prerequisites

You need **MPV** (or **VLC**) and **TorrServer** on your system.

- **Arch Linux / CachyOS**:
  ```bash
  sudo pacman -S mpv torrserver
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

### 2. Stream Series & Anime with Episode Grouping (`-s`)
Automatically detect seasons and episodes (e.g. `S01E01`, `1x01`, `Ep 01`, `Show - 01`), pick your episode, and auto-advance playback across the season:
```bash
tream -s jujutsu kaisen
tream -s "the bear" --buffer 1G
```

### 3. Download Torrent (`-d`)
Search, choose a result, and download files directly to a folder with a live progress bar showing transferred bytes, rate, and ETA:
```bash
tream -d bladerunner ~/Downloads
tream -d "ubuntu 24.04" ./isos
```

### 4. Watch History & Resume (`-h`)
View your recent streams, timestamps, and playback positions, and resume with a single keypress:
```bash
tream -h
```

### 5. Self-Update (`-U`)
Detects your installation method (git, pipx, or pip) and updates to the latest version:
```bash
tream -U
```

### 6. Buffer Size Override (`--buffer` / `-b`)
Configure memory cache size passed to both TorrServer and the player demuxer:
```bash
tream "dune 2" --buffer 512M
tream -s severance -b 1G
```

### 7. Player Selection (`--player` / `-p`)
Override player for a run:
```bash
tream avatar --player vlc
tream avatar --player mpv
```

### 8. JSON Scripting Output (`--json`)
Output search results as JSON on `stdout` without interactive prompts, while status messages remain on `stderr`:
```bash
tream --json "blade runner" | jq '.[0].magnet'
```

### 9. Configuration Management (`tream config`)
Read, edit, and validate configuration:
```bash
tream config get playback.buffer
tream config set playback.buffer 512M
tream config validate
tream config edit
```

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
preload_cache = 30
connections_limit = 800
enable_dht = true

[search]
providers = ["yts", "apibay", "nyaa"]
max_results = 20
fuzzy = true

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

Environment variables take highest precedence:
- `TREAM_BUFFER`: override default buffer size (e.g. `512M`, `1G`)
- `TREAM_PLAYER`: override default player (`mpv` or `vlc`)
- `TREAM_PORT`: override TorrServer HTTP port
- `TREAM_TORRSERVER_BIN`: custom TorrServer executable path

---

## Troubleshooting

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

---

## License

MIT License. Copyright (c) 2026 g1gowda123.
