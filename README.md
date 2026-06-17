<div align="center">

<br>

<img src="https://raw.githubusercontent.com/almezali/pkger-d/main/Screenshot-01.png" width="80" height="80" alt="PKGER-D Icon" style="border-radius: 18px;" />

# PKGER-D

### Professional Desktop Package Manager for Debian-based Linux

[![Version](https://img.shields.io/badge/version-1.04-6C63FF?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/almezali/pkger-d/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-00C896?style=for-the-badge&logo=gnu&logoColor=white)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-FFA500?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/almezali/pkger-d)
[![GTK](https://img.shields.io/badge/GTK-4.0-4A90D9?style=for-the-badge&logo=gnome&logoColor=white)](https://gtk.org)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)

<br>

> **Unified APT · Flatpak · Snap · AppImage management**  
> with Security Intelligence, System Health Monitoring & a beautiful modern UI

<br>

<a href="https://github.com/almezali/pkger-d/releases/download/1.04/pkger-d_1.04_x86_64.deb">
  <img src="https://img.shields.io/badge/⬇ Download-.DEB Package-0A74DA?style=for-the-badge&logo=debian&logoColor=white" alt="Download DEB" />
</a>
&nbsp;&nbsp;
<a href="https://github.com/almezali/pkger-d/releases/download/1.04/Pkger-x86_64.AppImage">
  <img src="https://img.shields.io/badge/⬇ Download-AppImage-00B388?style=for-the-badge&logo=appveyor&logoColor=white" alt="Download AppImage" />
</a>

<br><br>

</div>

---

## 📸 Screenshots

<div align="center">

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/almezali/pkger-d/main/Screenshot-01.png" width="280" alt="Dashboard" />
      <br><sub><b>🏠 Dashboard & Health Score</b></sub>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/almezali/pkger-d/main/Screenshot-02.png" width="280" alt="Package Search" />
      <br><sub><b>🔍 Multi-Source Package Search</b></sub>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/almezali/pkger-d/main/Screenshot-03.png" width="280" alt="Updates & Security" />
      <br><sub><b>🔒 Security Intelligence & Updates</b></sub>
    </td>
  </tr>
</table>

</div>

---

## ✨ Features

<table>
<tr>
<td width="50%">

### 📦 Unified Package Management
- **APT** — full Debian/Ubuntu package ecosystem
- **Flatpak** — sandboxed, cross-distro applications
- **Snap** — Canonical's universal package format
- **AppImage** — portable, no-install binaries
- One interface to rule them all

### 🔒 Security Intelligence
- Real-time **security severity** classification for updates
- Automatic detection of **security** vs **kernel** vs **important** updates
- Color-coded threat badges (`🔒 Security`, `⚙ Kernel`, `⬆ Important`)
- Pre-install conflict detection and architecture validation

### 🧠 DEB Package Analyzer
- Deep `.deb` file inspection before installation
- Full **dependency resolution** preview via APT simulation
- Detects missing dependencies, conflicts, and version mismatches
- Architecture compatibility checks (`amd64`, `arm64`, `all`)
- Drag-and-drop `.deb` file support

</td>
<td width="50%">

### 📊 System Health Dashboard
- **Health Score** (0–100%) computed from pending updates, security patches, held packages, and cache size
- Live system stats: kernel version, uptime, CPU load, disk space, RAM usage
- APT cache size monitoring with cleanup recommendations
- Distribution-aware (Ubuntu · Linux Mint · Debian)

### 🔍 Parallel Multi-Source Search
- Searches APT, Flatpak, and Snap **simultaneously** using ThreadPoolExecutor
- Up to 80 results per query, configurable
- Instant package info panel with version, size, and dependencies
- ⭐ Favorites system with persistent storage

### 📰 News & Updates Feed
- Live RSS feed (Ubuntu Blog / Linux Mint Blog) auto-detected per distro
- Stay informed about your distribution from inside the app

</td>
</tr>
</table>

---

## 🚀 Installation

### Option 1 — DEB Package *(Recommended for Ubuntu / Debian / Mint)*

```bash
wget https://github.com/almezali/pkger-d/releases/download/1.04/pkger-d_1.04_x86_64.deb
sudo apt install ./pkger-d_1.04_x86_64.deb
```

> APT will automatically resolve and install all required dependencies.

---

### Option 2 — AppImage *(Run anywhere, no installation needed)*

```bash
wget https://github.com/almezali/pkger-d/releases/download/1.04/Pkger-x86_64.AppImage
chmod +x Pkger-x86_64.AppImage
./Pkger-x86_64.AppImage
```

> The AppImage is fully self-contained. No root access required to run.

---

### Option 3 — Run from Source

```bash
# Install Python dependencies
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1

# Clone and run
git clone https://github.com/almezali/pkger-d.git
cd pkger-d
python3 pkger-d.py
```

---

## 🖥️ Usage

### Graphical Interface

Simply launch PKGER-D from your application menu, or run:

```bash
pkger-d
```

The GUI launches with a sidebar navigator and a full-featured dashboard.

---

### Command-Line Interface (CLI)

PKGER-D also ships a powerful CLI for automation and scripting:

```bash
# Search packages across APT, Flatpak, and Snap
pkger-d search firefox

# List all pending updates (with security classification)
pkger-d updates

# Refresh APT cache, then list updates
pkger-d updates --refresh

# Display system health score
pkger-d health

# Analyze a .deb file before installing
pkger-d deb /path/to/package.deb

# Analyze and immediately install a .deb
pkger-d deb /path/to/package.deb --install

# Install a package from a specific source
pkger-d install vlc --source apt
pkger-d install org.videolan.VLC --source flatpak
pkger-d install vlc --source snap
```

---

## 🗂️ Application Pages

| Page | Icon | Description |
|------|------|-------------|
| **Dashboard** | 🏠 | Health score, system stats, quick actions, distro news |
| **Search** | 🔍 | Unified search across APT · Flatpak · Snap |
| **Installed** | 📦 | Browse all installed packages, sort & filter |
| **Updates** | 🔄 | Pending updates with severity badges |
| **DEB Analyzer** | 🧩 | Deep inspection & safe install of `.deb` files |
| **AppImages** | 🖼️ | Scan & launch local AppImage files |
| **Repositories** | 🗄️ | View and manage APT sources & PPAs |
| **Logs** | 📋 | Terminal output, APT history, exportable logs |
| **Settings** | ⚙️ | Customize search sources, confirmations, and behavior |

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl + F` | Focus search bar |
| `F5` | Refresh current view |
| `F11` | Toggle fullscreen |
| `Ctrl + ,` | Open settings |
| `Ctrl + H` | View command history |

---

## 🛠️ System Requirements

| Requirement | Minimum |
|-------------|---------|
| **OS** | Ubuntu 22.04 · Linux Mint 21 · Debian 12 (or newer) |
| **Architecture** | x86_64 |
| **Python** | 3.12+ *(source only)* |
| **GTK** | 4.0+ |
| **libadwaita** | 1.0+ *(optional, enhances UI)* |
| **APT** | Required *(core feature)* |
| **Flatpak / Snap** | Optional *(auto-detected)* |

---

## ⚙️ Configuration

PKGER-D stores all configuration in `~/.config/pkger-d/`:

| File | Contents |
|------|----------|
| `settings.json` | App preferences |
| `history.json` | Command history |
| `favorites.json` | Starred packages |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `PKGER_DEBUG=1` | Enable verbose debug output |
| `PKGER_GSK_RENDERER=cairo` | Override GTK renderer (default: `cairo`) |
| `PKGER_USE_PORTAL=1` | Force GTK portal mode |

---

## 🏗️ Architecture

```
pkger-d/
├── Backend Logic
│   ├── APT integration      (dpkg-query, apt-cache, apt-get)
│   ├── Flatpak integration  (flatpak CLI)
│   ├── Snap integration     (snap CLI)
│   ├── DEB analyzer         (dpkg-deb + APT simulation)
│   └── System stats         (/proc/meminfo, /proc/uptime, df)
│
├── Security Engine
│   ├── Severity classifier  (security / kernel / important / normal)
│   ├── Conflict detector    (Conflicts:, Breaks: field analysis)
│   └── Architecture checker (dpkg --compare-versions)
│
└── GTK4 / Libadwaita UI
    ├── Sidebar navigation
    ├── Toast notifications
    ├── Parallel search (ThreadPoolExecutor)
    └── CSS-themed components
```

---

## 🤝 Contributing

Contributions are warmly welcome! Here's how to get started:

```bash
# Fork & clone
git clone https://github.com/almezali/pkger-d.git
cd pkger-d

# Create a feature branch
git checkout -b feature/my-improvement

# Make your changes, then submit a Pull Request
```

Please open an [issue](https://github.com/almezali/pkger-d/issues) before submitting large changes so we can coordinate.

---

## 📜 License

PKGER-D is released under the **GNU General Public License v3.0**.  
See [`LICENSE`](LICENSE) for the full text.

---

## 👤 Author

**almezali**  
GitHub: [@almezali](https://github.com/almezali)  
Project: [github.com/almezali/pkger-d](https://github.com/almezali/pkger-d)

---

<div align="center">

Made with ❤️ for the Linux community · © 2026 almezali

</div>
