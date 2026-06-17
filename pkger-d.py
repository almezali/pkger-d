#!/usr/bin/env python3
"""
PKGER-D — Professional desktop package manager for Ubuntu, Linux Mint, and Debian derivatives.
Unified APT · Flatpak · Snap · AppImage management with security intelligence and system health.
Optimized for Nuitka (Standalone/Onefile) & Python 3.12/3.13 compatibility.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Nuitka & Environment Fixes ---
IS_NUITKA = "__compiled__" in globals()
PKGER_DEBUG = (os.environ.get("PKGER_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")

def _configure_gtk_runtime():
    if "GSK_RENDERER" not in os.environ:
        os.environ["GSK_RENDERER"] = os.environ.get("PKGER_GSK_RENDERER", "cairo")
    if "GTK_USE_PORTAL" not in os.environ:
        use_portal = (os.environ.get("PKGER_USE_PORTAL") or "0").strip().lower()
        os.environ["GTK_USE_PORTAL"] = "1" if use_portal in ("1", "true", "yes", "on") else "0"

_configure_gtk_runtime()

def log_debug(msg):
    if PKGER_DEBUG:
        print(f"[DEBUG-PKGER-D] {msg}", file=sys.stderr)

def _prepend_env_path(var_name, path):
    if not path:
        return
    p = str(path)
    if not os.path.isdir(p):
        return
    current = os.environ.get(var_name, "")
    if current:
        if p not in current.split(":"):
            os.environ[var_name] = f"{p}:{current}"
    else:
        os.environ[var_name] = p

def _nuitka_bundle_dir():
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parent)
    except Exception:
        pass
    for key in ("NUITKA_ONEFILE_DIRECTORY", "NUITKA_PACKAGE_HOME"):
        val = os.environ.get(key)
        if val:
            candidates.append(Path(val))
    try:
        compiled = globals().get("__compiled__")
        if compiled is not None and hasattr(compiled, "containing_dir"):
            candidates.append(Path(compiled.containing_dir))
    except Exception:
        pass
    try:
        candidates.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass

    def _looks_like_bundle(p):
        if not p or not p.is_dir():
            return False
        return any((p / name).exists() for name in ("girepository-1.0", "lib", "share", "pkger-d"))

    for p in candidates:
        if _looks_like_bundle(p):
            return p
    return Path.cwd()

def _setup_nuitka_environment():
    if not IS_NUITKA:
        return
    try:
        bundle_dir = _nuitka_bundle_dir()
        if str(bundle_dir) not in sys.path:
            sys.path.insert(0, str(bundle_dir))
        typelib_path = bundle_dir / "girepository-1.0"
        if typelib_path.is_dir():
            _prepend_env_path("GI_TYPELIB_PATH", typelib_path)
        lib_path = bundle_dir / "lib"
        if lib_path.is_dir():
            _prepend_env_path("LD_LIBRARY_PATH", lib_path)
        share_dir = bundle_dir / "share"
        if share_dir.is_dir():
            _prepend_env_path("XDG_DATA_DIRS", share_dir)
            schemas = share_dir / "glib-2.0" / "schemas"
            if schemas.is_dir():
                os.environ.setdefault("GSETTINGS_SCHEMA_DIR", str(schemas))
    except Exception as exc:
        log_debug(f"Nuitka setup error: {exc}")

_setup_nuitka_environment()

try:
    import gi
    try:
        gi.require_version("Gtk", "4.0")
    except Exception:
        pass
    try:
        gi.require_version("Adw", "1")
    except Exception:
        pass
    from gi.repository import Gtk as Gtk4, Gdk as Gdk4, Gio as Gio4, GObject as GObject4, GLib, Pango
    try:
        from gi.repository import Adw
    except Exception:
        Adw = None
    GTK4_AVAILABLE = True
except Exception as e:
    GTK4_AVAILABLE = False
    print(f"Fatal: GTK4 not available: {e}", file=sys.stderr)
    sys.exit(1)

# --- App Constants ---
APP_NAME = "PKGER-D"
APP_VERSION = "1.04"
APP_DEVELOPER = "almezali"
APP_YEAR = "2026"
CONFIG_DIR = Path.home() / ".config" / "pkger-d"
CONFIG_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
FAVORITES_FILE = CONFIG_DIR / "favorites.json"

DEFAULT_SETTINGS = {
    "auto_check_updates": True,
    "show_security_alerts": True,
    "search_apt": True,
    "search_flatpak": True,
    "search_snap": True,
    "theme_accent": "default",
    "confirm_install": True,
    "confirm_remove": True,
    "max_search_results": 80,
    "language": "auto",
}

def detect_distro():
    info = {"id": "debian", "name": "Debian", "version": "", "codename": ""}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    v = v.strip('"')
                    if k == "ID":
                        info["id"] = v.lower()
                    elif k == "NAME":
                        info["name"] = v
                    elif k == "VERSION_ID":
                        info["version"] = v
                    elif k == "VERSION_CODENAME":
                        info["codename"] = v
        if "mint" in info["id"] or "mint" in info["name"].lower():
            info["id"] = "mint"
        elif "ubuntu" in info["id"]:
            info["id"] = "ubuntu"
    except Exception:
        pass
    return info

DISTRO_INFO = detect_distro()
DISTRO = DISTRO_INFO["id"]
NEWS_RSS = "https://blog.linuxmint.com/?feed=rss2" if DISTRO == "mint" else "https://ubuntu.com/blog/feed"

# --- Config & Persistence ---
class AppConfig:
    _lock = threading.Lock()

    @classmethod
    def load(cls):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = dict(DEFAULT_SETTINGS)
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE) as f:
                    data.update(json.load(f))
        except Exception:
            pass
        return data

    @classmethod
    def save(cls, settings):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with cls._lock:
            with open(CONFIG_FILE, "w") as f:
                json.dump(settings, f, indent=2)

    @classmethod
    def load_json(cls, path, default=None):
        try:
            if path.exists():
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return default if default is not None else []

    @classmethod
    def save_json(cls, path, data):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with cls._lock:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)

# --- Utility Functions ---
def command_exists(cmd):
    return subprocess.run(["which", cmd], capture_output=True).returncode == 0

def run_command(cmd, timeout=120):
    try:
        return subprocess.run(
            cmd if isinstance(cmd, list) else shlex.split(cmd),
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=str(e))

def human_size(nbytes):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"

def classify_update_severity(name, repository, source):
    repo_l = (repository or "").lower()
    name_l = (name or "").lower()
    if source == "apt":
        if "-security" in repo_l or "/security" in repo_l:
            return "security"
        if any(k in name_l for k in ("linux-image", "linux-headers", "linux-modules")):
            return "kernel"
        if any(k in repo_l for k in ("updates", "upgrades")):
            return "important"
    return "normal"

# --- Backend Logic ---
def fetch_installed_packages():
    pkgs = []
    try:
        r = run_command(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Section}\t${Description}\t${Installed-Size}\n"], timeout=90)
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                pkgs.append({
                    "name": parts[0], "pkg": parts[0], "version": parts[1],
                    "repository": parts[2] if len(parts) > 2 else "installed",
                    "description": parts[3] if len(parts) > 3 else "-",
                    "size_kb": int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0,
                    "installed": True, "source": "apt",
                })
    except Exception:
        pass
    return pkgs

def fetch_apt_updates():
    updates = []
    try:
        r = run_command(["apt", "list", "--upgradable"], timeout=90)
        for line in r.stdout.splitlines():
            if "/" in line and "upgradable from:" in line:
                m = re.match(r"^([^/]+)/(\S+)\s+(\S+)\s+(\S+)\s+\[upgradable from:\s+(\S+)\]", line)
                if m:
                    name, arch, to_ver, repo, from_ver = m.groups()
                    sev = classify_update_severity(name, repo, "apt")
                    updates.append({
                        "name": name, "pkg": name, "arch": arch,
                        "from": from_ver, "to": to_ver, "repository": repo,
                        "source": "apt", "severity": sev, "installed": True,
                    })
    except Exception:
        pass
    return updates

def fetch_flatpak_updates():
    updates = []
    if not command_exists("flatpak"):
        return updates
    try:
        r = run_command(["flatpak", "remote-ls", "--updates", "--columns=application,version,origin,name"], timeout=90)
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                updates.append({
                    "name": parts[3] if len(parts) > 3 else parts[0], "pkg": parts[0],
                    "from": "-", "to": parts[1], "repository": parts[2],
                    "source": "flatpak", "severity": "normal", "installed": True,
                })
    except Exception:
        pass
    return updates

def fetch_snap_updates():
    updates = []
    if not command_exists("snap"):
        return updates
    try:
        r = run_command(["snap", "refresh", "--list"], timeout=90)
        lines = r.stdout.splitlines()
        if len(lines) > 1:
            for line in lines[1:]:
                parts = re.split(r"\s{2,}", line.strip())
                if len(parts) >= 4:
                    updates.append({
                        "name": parts[0], "pkg": parts[0], "from": parts[1], "to": parts[2],
                        "repository": "snapcraft", "source": "snap", "severity": "normal", "installed": True,
                    })
    except Exception:
        pass
    return updates

def fetch_all_pending_updates(refresh_metadata=False):
    if refresh_metadata:
        run_command(["pkexec", "apt", "update"], timeout=180)
    return fetch_apt_updates() + fetch_flatpak_updates() + fetch_snap_updates()

def fetch_held_packages():
    held = []
    try:
        r = run_command(["apt-mark", "showhold"], timeout=30)
        for line in r.stdout.splitlines():
            name = line.strip()
            if name:
                held.append({"name": name, "pkg": name, "source": "apt", "reason": "held"})
    except Exception:
        pass
    return held

def get_apt_cache_size():
    total = 0
    for d in ("/var/cache/apt/archives", "/var/lib/apt/lists"):
        p = Path(d)
        if p.is_dir():
            try:
                for f in p.rglob("*"):
                    if f.is_file():
                        total += f.stat().st_size
            except Exception:
                pass
    return total

def get_system_stats():
    stats = {
        "kernel": subprocess.getoutput("uname -r"),
        "uptime": "-",
        "load": "-",
        "disk_free": "-",
        "mem_used_pct": "-",
        "cache_size": get_apt_cache_size(),
    }
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
            days, rem = divmod(int(secs), 86400)
            hrs, rem = divmod(rem, 3600)
            mins = rem // 60
            stats["uptime"] = f"{days}d {hrs}h {mins}m" if days else f"{hrs}h {mins}m"
    except Exception:
        pass
    try:
        stats["load"] = " ".join(open("/proc/loadavg").read().split()[:3])
    except Exception:
        pass
    try:
        r = run_command(["df", "-h", "/"], timeout=10)
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                stats["disk_free"] = parts[3]
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                k, v = line.split(":", 1)
                mem[k.strip()] = int(v.split()[0])
            total = mem.get("MemTotal", 1)
            avail = mem.get("MemAvailable", mem.get("MemFree", 0))
            stats["mem_used_pct"] = f"{100 - int(avail * 100 / total)}%"
    except Exception:
        pass
    return stats

def compute_health_score(installed_count, updates, held, stats):
    score = 100
    security = sum(1 for u in updates if u.get("severity") == "security")
    kernel = sum(1 for u in updates if u.get("severity") == "kernel")
    if security:
        score -= min(30, security * 8)
    if kernel:
        score -= min(15, kernel * 5)
    if len(updates) > 50:
        score -= 10
    elif len(updates) > 20:
        score -= 5
    if held:
        score -= min(10, len(held) * 2)
    cache_gb = stats.get("cache_size", 0) / (1024 ** 3)
    if cache_gb > 2:
        score -= 10
    elif cache_gb > 1:
        score -= 5
    return max(0, min(100, score))

def search_apt(query, limit=50):
    results = []
    try:
        r = run_command(["apt-cache", "search", query], timeout=60)
        for line in r.stdout.splitlines()[:limit]:
            if " - " in line:
                n, d = line.split(" - ", 1)
                results.append({"name": n.strip(), "desc": d.strip(), "source": "apt"})
    except Exception:
        pass
    return results

def search_flatpak(query, limit=20):
    results = []
    if not command_exists("flatpak"):
        return results
    try:
        r = run_command(["flatpak", "search", "--columns=application,description,version", query], timeout=60)
        for line in r.stdout.splitlines()[:limit]:
            parts = line.split("\t")
            if len(parts) >= 2:
                results.append({
                    "name": parts[0].strip(),
                    "desc": parts[1].strip() if len(parts) > 1 else "",
                    "version": parts[2].strip() if len(parts) > 2 else "",
                    "source": "flatpak",
                })
    except Exception:
        pass
    return results

def search_snap(query, limit=20):
    results = []
    if not command_exists("snap"):
        return results
    try:
        r = run_command(["snap", "find", query], timeout=60)
        for line in r.stdout.splitlines()[1:limit + 1]:
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 2:
                results.append({
                    "name": parts[0].strip(),
                    "desc": parts[2].strip() if len(parts) > 2 else parts[1].strip(),
                    "version": parts[1].strip() if len(parts) > 2 else "",
                    "source": "snap",
                })
    except Exception:
        pass
    return results

def parallel_search(query, settings):
    results = []
    tasks = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        if settings.get("search_apt", True):
            tasks[pool.submit(search_apt, query, settings.get("max_search_results", 80) // 2)] = "apt"
        if settings.get("search_flatpak", True):
            tasks[pool.submit(search_flatpak, query, 25)] = "flatpak"
        if settings.get("search_snap", True):
            tasks[pool.submit(search_snap, query, 25)] = "snap"
        for fut in as_completed(tasks):
            try:
                results.extend(fut.result())
            except Exception:
                pass
    return results[: settings.get("max_search_results", 80)]

def fetch_package_info(name, source="apt"):
    info = {"name": name, "source": source, "description": "", "version": "", "depends": [], "size": ""}
    try:
        if source == "apt":
            r = run_command(["apt-cache", "show", name], timeout=30)
            for line in r.stdout.splitlines():
                if line.startswith("Description:"):
                    info["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    info["version"] = line.split(":", 1)[1].strip()
                elif line.startswith("Installed-Size:"):
                    info["size"] = line.split(":", 1)[1].strip() + " KB"
                elif line.startswith("Depends:"):
                    deps = line.split(":", 1)[1].strip()
                    info["depends"] = [d.strip().split()[0] for d in deps.split(",")[:15]]
        elif source == "flatpak" and command_exists("flatpak"):
            r = run_command(["flatpak", "info", name], timeout=30)
            info["description"] = r.stdout[:500]
        elif source == "snap" and command_exists("snap"):
            r = run_command(["snap", "info", name], timeout=30)
            info["description"] = r.stdout[:500]
    except Exception:
        pass
    return info

def scan_appimages(query=""):
    home = Path.home()
    out = []
    q = query.lower()
    skip = {".cache", ".local/share/Trash", "node_modules", ".git"}
    try:
        for root, dirs, files in os.walk(home):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for f in files:
                if f.lower().endswith(".appimage"):
                    p = Path(root) / f
                    if q and q not in f.lower():
                        continue
                    size = p.stat().st_size / (1024 * 1024)
                    out.append({
                        "name": p.stem, "pkg": p.name, "path": str(p),
                        "version": f"{size:.1f} MB", "repository": "local",
                        "description": str(p.parent), "installed": True, "source": "appimage",
                    })
    except Exception:
        pass
    return out

def list_ppa_sources():
    repos = []
    try:
        main = Path("/etc/apt/sources.list")
        if main.exists():
            repos.append({"name": "main", "file": str(main), "type": "system"})
        for f in sorted(Path("/etc/apt/sources.list.d").glob("*")):
            if f.suffix in (".list", ".sources"):
                content = f.read_text(errors="ignore")
                is_ppa = "ppa:" in content.lower() or "launchpad" in content.lower()
                repos.append({
                    "name": f.stem, "file": str(f), "type": "ppa" if is_ppa else "repo",
                    "enabled": not f.name.endswith(".disabled"),
                })
    except Exception:
        pass
    return repos

def fetch_apt_history(limit=50):
    entries = []
    try:
        path = Path("/var/log/apt/history.log")
        if path.exists():
            lines = path.read_text(errors="ignore").splitlines()
            block = []
            for line in reversed(lines):
                if line.startswith("Start-Date:"):
                    if block:
                        entries.append("\n".join(reversed(block)))
                        if len(entries) >= limit:
                            break
                    block = [line]
                elif block:
                    block.append(line)
            if block and len(entries) < limit:
                entries.append("\n".join(reversed(block)))
    except Exception:
        pass
    return entries

# --- DEB Package Intelligence ---
def deb_field(deb_path, field_name):
    r = run_command(["dpkg-deb", "-f", str(deb_path), field_name], timeout=30)
    return r.stdout.strip() if r.returncode == 0 else ""

def pkg_is_installed(name):
    r = run_command(["dpkg-query", "-W", "-f=${Status}", name], timeout=10)
    return r.returncode == 0 and "install ok installed" in r.stdout

def pkg_get_version(name):
    r = run_command(["dpkg-query", "-W", "-f=${Version}", name], timeout=10)
    return r.stdout.strip() if r.returncode == 0 else None

def dpkg_version_compare(a, op, b):
    r = run_command(["dpkg", "--compare-versions", a, op, b], timeout=10)
    return r.returncode == 0

def _dep_pkg_name(clause_part):
    name = re.sub(r"\s*\([^)]*\)", "", clause_part.strip()).strip()
    return name.split()[0] if name else ""

def _check_dep_clause(clause):
    clause = clause.strip()
    if not clause or clause.startswith("${"):
        return None
    alternatives = [a.strip() for a in clause.split("|")]
    for alt in alternatives:
        name = _dep_pkg_name(alt)
        if not name or name.startswith("${"):
            continue
        if pkg_is_installed(name):
            return {
                "clause": clause, "status": "ok", "name": name,
                "version": pkg_get_version(name), "type_label": "installed",
            }
    missing = [_dep_pkg_name(a) for a in alternatives if _dep_pkg_name(a) and not _dep_pkg_name(a).startswith("${")]
    return {
        "clause": clause, "status": "missing", "name": missing[0] if missing else clause,
        "alternatives": missing, "type_label": "missing",
    }

def _analyze_dep_field(dep_str, dep_type):
    out = []
    if not dep_str:
        return out
    for clause in dep_str.split(","):
        item = _check_dep_clause(clause)
        if item:
            item["dep_type"] = dep_type
            out.append(item)
    return out

def parse_apt_simulate(output):
    extra_new, extra_upgraded = [], []
    mode = None
    for line in output.splitlines():
        low = line.lower()
        if "the following new packages will be installed" in low:
            mode = "new"
            tail = line.split(":", 1)
            if len(tail) > 1 and tail[1].strip():
                extra_new.extend(tail[1].split())
            continue
        if "the following packages will be upgraded" in low:
            mode = "upgrade"
            tail = line.split(":", 1)
            if len(tail) > 1 and tail[1].strip():
                extra_upgraded.extend(tail[1].split())
            continue
        if "the following additional packages will be installed" in low:
            mode = "new"
            continue
        if mode and line.startswith("  "):
            extra_new.extend(line.split())
        elif mode and not line.strip():
            mode = None
        elif line.strip() and not line.startswith(" "):
            mode = None
    return extra_new, extra_upgraded

def analyze_deb_file(deb_path):
    path = Path(deb_path).resolve()
    if not path.is_file() or path.suffix.lower() != ".deb":
        return {"error": "Not a valid .deb file", "path": str(path)}

    meta_fields = (
        "Package", "Version", "Architecture", "Maintainer", "Description",
        "Depends", "Pre-Depends", "Recommends", "Suggests", "Conflicts", "Breaks",
        "Installed-Size", "Section", "Homepage",
    )
    fields = {f: deb_field(path, f) for f in meta_fields}
    pkg = fields.get("Package") or path.stem
    version = fields.get("Version", "?")
    deb_arch = fields.get("Architecture", "")
    sys_arch = run_command(["dpkg", "--print-architecture"]).stdout.strip() or "amd64"
    arch_ok = deb_arch in ("all", sys_arch, "") or deb_arch == sys_arch

    installed = pkg_is_installed(pkg)
    installed_ver = pkg_get_version(pkg) if installed else None
    is_upgrade = False
    if installed and installed_ver and version not in ("?", ""):
        try:
            is_upgrade = dpkg_version_compare(version, "gt", installed_ver)
        except Exception:
            is_upgrade = version != installed_ver

    depends = _analyze_dep_field(fields.get("Depends", ""), "Depends")
    depends += _analyze_dep_field(fields.get("Pre-Depends", ""), "Pre-Depends")
    recommends = _analyze_dep_field(fields.get("Recommends", ""), "Recommends")

    conflicts = []
    for ctype in ("Conflicts", "Breaks"):
        for clause in (fields.get(ctype, "") or "").split(","):
            clause = clause.strip()
            if not clause:
                continue
            name = _dep_pkg_name(clause)
            if name and pkg_is_installed(name):
                conflicts.append({"package": name, "type": ctype, "version": pkg_get_version(name)})

    try:
        file_size = human_size(path.stat().st_size)
    except Exception:
        file_size = "?"

    try:
        inst_kb = int(fields.get("Installed-Size") or 0)
        installed_size = human_size(inst_kb * 1024) if inst_kb else "?"
    except Exception:
        installed_size = "?"

    sim = run_command(["apt-get", "install", "--simulate", "-y", str(path)], timeout=120)
    simulate_text = (sim.stdout or "") + (sim.stderr or "")
    simulate_ok = sim.returncode == 0
    extra_new, extra_upgraded = parse_apt_simulate(simulate_text)

    missing_deps = [d for d in depends if d.get("status") == "missing"]
    warnings = []
    if not arch_ok:
        warnings.append(f"Architecture mismatch: package is {deb_arch}, system is {sys_arch}")
    if conflicts:
        warnings.append(f"{len(conflicts)} conflict(s) detected with installed packages")
    if missing_deps and not extra_new:
        warnings.append(f"{len(missing_deps)} dependency clause(s) not satisfied locally")
    if installed and not is_upgrade:
        warnings.append(f"Already installed: {pkg} {installed_ver}")
    if "Unable to locate package" in simulate_text:
        warnings.append("Some dependencies may not be available in your repositories")

    ready = arch_ok and not conflicts and simulate_ok and not (installed and not is_upgrade)

    return {
        "path": str(path),
        "filename": path.name,
        "package": pkg,
        "version": version,
        "fields": fields,
        "arch_ok": arch_ok,
        "deb_arch": deb_arch,
        "system_arch": sys_arch,
        "file_size": file_size,
        "installed_size": installed_size,
        "installed": installed,
        "installed_version": installed_ver,
        "is_upgrade": is_upgrade,
        "depends": depends,
        "recommends": recommends,
        "conflicts": conflicts,
        "missing_deps": missing_deps,
        "extra_new": extra_new,
        "extra_upgraded": extra_upgraded,
        "simulate_ok": simulate_ok,
        "simulate_output": simulate_text.strip(),
        "warnings": warnings,
        "ready": ready,
        "summary": _deb_summary(ready, pkg, version, missing_deps, extra_new, conflicts, installed, is_upgrade),
    }

def _deb_summary(ready, pkg, version, missing, extra_new, conflicts, installed, is_upgrade):
    if conflicts:
        return f"Cannot install — conflicts with {conflicts[0]['package']}"
    if installed and not is_upgrade:
        return f"{pkg} {version} is already installed"
    if is_upgrade:
        return f"Upgrade available → {pkg} {version}"
    if extra_new:
        return f"Ready — will install {pkg} + {len(extra_new)} dependency package(s)"
    if missing:
        return f"Ready — {len(missing)} dependencies will be resolved via APT"
    if ready:
        return f"Ready to install {pkg} {version}"
    return "Review warnings before installing"

def build_deb_install_command(deb_path):
    return f"apt-get install -y {shlex.quote(str(Path(deb_path).resolve()))}"

SEVERITY_COLORS = {
    "security": "#e74c3c",
    "kernel": "#e67e22",
    "important": "#3498db",
    "normal": "#95a5a6",
}

SEVERITY_LABELS = {
    "security": "🔒 Security",
    "kernel": "⚙ Kernel",
    "important": "⬆ Important",
    "normal": "Update",
}

# --- UI Classes ---
class PkgerDWindow(Adw.ApplicationWindow if Adw else Gtk4.Window):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title(f"{APP_NAME} — {DISTRO_INFO['name']}")
        self.set_default_size(800, 800)

        self.settings = AppConfig.load()
        self.installed_pkgs = []
        self.updates_data = []
        self.held_packages = []
        self.system_stats = {}
        self.command_history = AppConfig.load_json(HISTORY_FILE, [])
        self.favorites = set(AppConfig.load_json(FAVORITES_FILE, []))
        self.deb_analysis = None
        self.deb_file_path = None
        self._search_timer = None
        self._active_worker = None

        self._setup_ui()
        self._apply_css()
        self._setup_shortcuts()
        self._load_initial_data()

    def _apply_css(self):
        css = """
        .card { background-color: alpha(@theme_bg_color, 0.45); border: 1px solid alpha(@theme_fg_color, 0.08);
                border-radius: 14px; padding: 16px; transition: all 200ms ease; }
        .card:hover { border-color: alpha(@accent_color, 0.3); }
        .dash-card { padding: 10px 12px; border-radius: 10px; min-width: 108px; }
        .dash-card-title { font-size: 10px; font-weight: 600; letter-spacing: 0.4px;
                           color: alpha(@theme_fg_color, 0.5); text-transform: uppercase; }
        .dash-card-value { font-size: 15px; font-weight: 700; margin-top: 2px; }
        .dash-health-card { padding: 12px 14px; border-radius: 12px; min-width: 120px; }
        .dash-health-value { font-size: 26px; font-weight: 800; line-height: 1; }
        .dash-welcome { font-size: 20px; font-weight: 700; }
        .dash-subtitle { font-size: 12px; }
        .dash-actions button { padding: 6px 14px; font-size: 12px; border-radius: 20px; }
        .dash-stats-line { font-size: 11px; padding: 8px 12px; border-radius: 8px;
                           background: alpha(@theme_fg_color, 0.04); }
        .card-health { padding: 20px; border-radius: 18px; }
        .sidebar { background-color: alpha(@theme_bg_color, 0.82);
                   border-right: 1px solid alpha(@theme_fg_color, 0.06); padding: 6px 0; }
        .nav-section { font-size: 9px; font-weight: 700; letter-spacing: 1.2px;
                       color: alpha(@theme_fg_color, 0.38); margin: 10px 14px 4px 14px; }
        .nav-item { padding: 0; margin: 1px 8px; border-radius: 8px; background: transparent;
                    border: none; box-shadow: none; min-height: 34px; }
        .nav-item:hover { background-color: alpha(@theme_fg_color, 0.06); }
        .nav-item.active { background-color: alpha(@accent_color, 0.14); color: @accent_color; }
        .nav-item.active .nav-label { font-weight: 700; }
        .nav-row { padding: 6px 10px; }
        .nav-label { font-size: 12px; font-weight: 500; }
        .nav-icon { opacity: 0.85; min-width: 18px; }
        .nav-item.active .nav-icon { opacity: 1; }
        .sidebar-brand { padding: 14px 14px 10px 14px; }
        .sidebar-brand-name { font-size: 15px; font-weight: 800; letter-spacing: 0.6px; }
        .sidebar-brand-ver { font-size: 10px; opacity: 0.42; margin-top: 1px; }
        .header-title { font-weight: 700; font-size: 13px; opacity: 0.9; margin-start: 4px; }
        .header-menu-btn { border-radius: 50%; padding: 6px; }
        .deb-summary-ok { color: #2ecc71; font-weight: 700; font-size: 13px; }
        .deb-summary-warn { color: #f39c12; font-weight: 700; font-size: 13px; }
        .deb-summary-error { color: #e74c3c; font-weight: 700; font-size: 13px; }
        .deb-meta { font-size: 11px; color: alpha(@theme_fg_color, 0.55); }
        .deb-meta-val { font-size: 12px; font-weight: 600; }
        .deb-dep-ok { color: #2ecc71; font-size: 11px; font-weight: 600; }
        .deb-dep-miss { color: #e74c3c; font-size: 11px; font-weight: 600; }
        .deb-drop-hint { font-size: 12px; opacity: 0.55; padding: 20px; border: 1px dashed alpha(@theme_fg_color, 0.15);
                         border-radius: 10px; }
        .nav-btn { padding: 11px 14px; margin: 2px 8px; border-radius: 10px; font-weight: 600; text-align: left; }
        .nav-btn:hover { background-color: alpha(@theme_fg_color, 0.06); }
        .nav-btn.active { background-color: @theme_selected_bg_color; color: @theme_selected_fg_color; }
        .section-title { font-weight: 800; font-size: 15px; }
        .dim-label { color: alpha(@theme_fg_color, 0.55); font-size: 13px; }
        .terminal-view { font-family: monospace; font-size: 13px; background-color: #1a1a2e; color: #e0e0e0; padding: 12px; border-radius: 8px; }
        .status-bar { border-top: 1px solid alpha(@theme_fg_color, 0.08); padding: 6px 14px; font-size: 12px; }
        .health-excellent { color: #2ecc71; font-weight: 800; }
        .health-good { color: #3498db; font-weight: 800; }
        .health-warning { color: #f39c12; font-weight: 800; }
        .health-critical { color: #e74c3c; font-weight: 800; }
        .badge-security { background: alpha(#e74c3c, 0.15); color: #e74c3c; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700; }
        .badge-kernel { background: alpha(#e67e22, 0.15); color: #e67e22; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700; }
        .badge-normal { background: alpha(@theme_fg_color, 0.08); border-radius: 6px; padding: 2px 8px; font-size: 11px; }
        .search-spinner { opacity: 0.7; }
        .logo-text { font-weight: 900; font-size: 18px; letter-spacing: 1px; }
        .version-text { font-size: 11px; opacity: 0.5; }
        """
        provider = Gtk4.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk4.StyleContext.add_provider_for_display(
            Gdk4.Display.get_default(), provider, Gtk4.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _setup_ui(self):
        header = Gtk4.HeaderBar()
        header.set_show_title_buttons(True)
        self._build_header_menu(header)
        self.set_titlebar(header)

        body = Gtk4.Box(orientation=Gtk4.Orientation.HORIZONTAL)
        body.set_vexpand(True)

        if Adw:
            self.toast_overlay = Adw.ToastOverlay()
            self.toast_overlay.set_child(body)
            self.set_content(self.toast_overlay)
        else:
            self.set_child(body)
            self.toast_overlay = None

        self.sidebar_revealer = Gtk4.Revealer()
        self.sidebar_revealer.set_transition_type(Gtk4.RevealerTransitionType.SLIDE_RIGHT)
        self.sidebar_revealer.set_reveal_child(True)

        self.sidebar = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=0)
        self.sidebar.set_size_request(168, -1)
        self.sidebar.add_css_class("sidebar")
        self.sidebar_revealer.set_child(self.sidebar)
        body.append(self.sidebar_revealer)

        brand = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=0)
        brand.add_css_class("sidebar-brand")
        brand_name = Gtk4.Label(label=APP_NAME)
        brand_name.add_css_class("sidebar-brand-name")
        brand_name.set_xalign(0)
        brand_ver = Gtk4.Label(label=f"v{APP_VERSION}")
        brand_ver.add_css_class("sidebar-brand-ver")
        brand_ver.set_xalign(0)
        brand.append(brand_name)
        brand.append(brand_ver)
        self.sidebar.append(brand)

        self.nav_btns = {}
        nav_sections = [
            ("MAIN", [
                ("dash", "Home", "computer-symbolic"),
            ]),
            ("PACKAGES", [
                ("install", "Install", "system-software-install-symbolic"),
                ("deb", ".deb", "application-x-deb-symbolic"),
                ("installed", "Installed", "folder-download-symbolic"),
                ("updates", "Updates", "software-update-available-symbolic"),
                ("security", "Security", "security-high-symbolic"),
            ]),
            ("SYSTEM", [
                ("repos", "Sources", "network-server-symbolic"),
                ("appimages", "Images", "package-x-generic-symbolic"),
                ("system", "Tools", "system-run-symbolic"),
                ("settings", "Settings", "preferences-system-symbolic"),
            ]),
            ("MORE", [
                ("logs", "Logs", "utilities-terminal-symbolic"),
                ("news", "News", "dialog-information-symbolic"),
            ]),
        ]

        scroll_nav = Gtk4.ScrolledWindow()
        scroll_nav.set_policy(Gtk4.PolicyType.NEVER, Gtk4.PolicyType.AUTOMATIC)
        scroll_nav.set_vexpand(True)
        nav_box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=0)
        scroll_nav.set_child(nav_box)
        self.sidebar.append(scroll_nav)

        for section_title, items in nav_sections:
            self._add_nav_section(nav_box, section_title)
            for name, label, icon in items:
                nav_box.append(self._create_nav_item(name, label, icon))

        self.content_area = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL)
        self.content_area.set_hexpand(True)
        body.append(self.content_area)

        self.toolbar = Gtk4.Box(orientation=Gtk4.Orientation.HORIZONTAL, spacing=10)
        self.toolbar.set_margin_top(6)
        self.toolbar.set_margin_bottom(6)
        self.toolbar.set_margin_start(14)
        self.toolbar.set_margin_end(14)

        self.page_title_lbl = Gtk4.Label(label="Home")
        self.page_title_lbl.add_css_class("section-title")
        self.page_title_lbl.set_hexpand(True)
        self.page_title_lbl.set_xalign(0)
        self.toolbar.append(self.page_title_lbl)
        self.content_area.append(self.toolbar)

        self.stack = Gtk4.Stack()
        self.stack.set_transition_type(Gtk4.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)
        self.content_area.append(self.stack)
        self._init_pages()

        self.bottom_bar = Gtk4.Box(orientation=Gtk4.Orientation.HORIZONTAL, spacing=12)
        self.bottom_bar.add_css_class("status-bar")
        self.status_lbl = Gtk4.Label(label="Ready")
        self.status_lbl.set_xalign(0)
        self.bottom_bar.append(self.status_lbl)
        self.progress_bar = Gtk4.ProgressBar()
        self.progress_bar.set_hexpand(True)
        self.progress_bar.set_show_text(True)
        self.bottom_bar.append(self.progress_bar)
        self.content_area.append(self.bottom_bar)
        self._switch_page("dash")

    def _add_nav_section(self, parent, title):
        lbl = Gtk4.Label(label=title)
        lbl.add_css_class("nav-section")
        lbl.set_xalign(0)
        parent.append(lbl)

    def _create_nav_item(self, name, label, icon):
        row = Gtk4.Box(orientation=Gtk4.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("nav-row")
        icon_img = Gtk4.Image.new_from_icon_name(icon)
        icon_img.set_pixel_size(16)
        icon_img.add_css_class("nav-icon")
        text = Gtk4.Label(label=label)
        text.add_css_class("nav-label")
        text.set_xalign(0)
        text.set_hexpand(True)
        row.append(icon_img)
        row.append(text)

        btn = Gtk4.Button()
        btn.add_css_class("nav-item")
        btn.set_child(row)
        btn.set_tooltip_text(label)
        btn.connect("clicked", lambda _x, n=name: self._switch_page(n))
        self.nav_btns[name] = btn
        return btn

    def _build_header_menu(self, header):
        title = Gtk4.Label(label=APP_NAME)
        title.add_css_class("header-title")
        header.pack_start(title)

        search_btn = Gtk4.Button(icon_name="system-search-symbolic")
        search_btn.set_tooltip_text("Search (Ctrl+F)")
        search_btn.connect("clicked", lambda _x: self._focus_search())
        header.pack_end(search_btn)

        refresh_btn = Gtk4.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh (Ctrl+R)")
        refresh_btn.connect("clicked", lambda _x: self._refresh_current_page())
        header.pack_end(refresh_btn)

        menu_btn = Gtk4.MenuButton()
        menu_btn.add_css_class("header-menu-btn")
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_tooltip_text("Menu")

        menu = Gio4.Menu()

        maint = Gio4.Menu()
        maint.append("Upgrade All", "win.upgrade")
        maint.append("Clean Cache", "win.clean")
        maint.append("Fix Broken", "win.fix")
        menu.append_submenu("Maintenance", maint)

        hist = Gio4.Menu()
        hist.append("Command History", "win.history")
        hist.append("APT History", "win.apt-history")
        menu.append_submenu("History", hist)

        view = Gio4.Menu()
        view.append("Toggle Sidebar", "win.toggle-sidebar")
        view.append("Fullscreen", "win.fullscreen")
        menu.append_submenu("View", view)

        app_menu = Gio4.Menu()
        app_menu.append("About", "win.about")
        app_menu.append("Quit", "app.quit")
        menu.append_section(None, app_menu)

        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        self._add_action("about", self._show_about)
        self._add_action("history", self._show_history)
        self._add_action("apt-history", self._show_apt_history)
        self._add_action("fullscreen", self._toggle_fullscreen)
        self._add_action("toggle-sidebar", self._toggle_sidebar)
        self._add_action("upgrade", lambda: self._quick_action("upgrade"))
        self._add_action("clean", lambda: self._quick_action("clean"))
        self._add_action("fix", lambda: self._quick_action("fix"))

    def _toggle_sidebar(self):
        self.sidebar_revealer.set_reveal_child(not self.sidebar_revealer.get_reveal_child())

    def _ensure_win_action_group(self):
        if getattr(self, "_win_action_group", None) is not None:
            return
        self._win_action_group = Gio4.SimpleActionGroup()
        self.insert_action_group("win", self._win_action_group)

    def _add_action(self, name, callback):
        self._ensure_win_action_group()
        action = Gio4.SimpleAction.new(name, None)
        action.connect("activate", lambda _a, _p: callback())
        self._win_action_group.add_action(action)

    def _setup_shortcuts(self):
        ctrl = Gdk4.ModifierType.CONTROL_MASK
        self.add_shortcut(Gtk4.Shortcut.new(
            Gtk4.ShortcutTrigger.parse_string("<Primary>f"),
            Gtk4.CallbackAction.new(lambda *_: self._focus_search() or True),
        ))
        self.add_shortcut(Gtk4.Shortcut.new(
            Gtk4.ShortcutTrigger.parse_string("<Primary>r"),
            Gtk4.CallbackAction.new(lambda *_: self._refresh_current_page() or True),
        ))
        self.add_shortcut(Gtk4.Shortcut.new(
            Gtk4.ShortcutTrigger.parse_string("F11"),
            Gtk4.CallbackAction.new(lambda *_: self._toggle_fullscreen() or True),
        ))

    def _focus_search(self):
        self._switch_page("install")
        self.search_entry.grab_focus()

    def _init_pages(self):
        self.stack.add_named(self._create_dash_page(), "dash")
        self.stack.add_named(self._create_install_page(), "install")
        self.stack.add_named(self._create_deb_page(), "deb")
        self.stack.add_named(self._create_installed_page(), "installed")
        self.stack.add_named(self._create_updates_page(), "updates")
        self.stack.add_named(self._create_security_page(), "security")
        self.stack.add_named(self._create_repos_page(), "repos")
        self.stack.add_named(self._create_appimages_page(), "appimages")
        self.stack.add_named(self._create_system_page(), "system")
        self.stack.add_named(self._create_settings_page(), "settings")
        self.stack.add_named(self._create_logs_page(), "logs")
        self.stack.add_named(self._create_news_page(), "news")

    def _create_dash_page(self):
        scroll = Gtk4.ScrolledWindow()
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(24)
        box.set_margin_end(24)
        scroll.set_child(box)

        head_box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=4)
        welcome = Gtk4.Label(label=f"Welcome to {APP_NAME}")
        welcome.add_css_class("dash-welcome")
        welcome.set_xalign(0)
        head_box.append(welcome)

        sub = Gtk4.Label(
            label=f"{DISTRO_INFO['name']} {DISTRO_INFO['version']} — Intelligent Package Management",
        )
        sub.add_css_class("dash-subtitle")
        sub.add_css_class("dim-label")
        sub.set_xalign(0)
        head_box.append(sub)
        box.append(head_box)

        cards_row = Gtk4.Box(orientation=Gtk4.Orientation.HORIZONTAL, spacing=10)
        cards_row.set_homogeneous(False)

        health_card = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=4)
        health_card.add_css_class("card")
        health_card.add_css_class("dash-health-card")
        hl = Gtk4.Label(label="Health")
        hl.add_css_class("dash-card-title")
        hl.set_xalign(0)
        self.dash_health_lbl = Gtk4.Label(label="—")
        self.dash_health_lbl.add_css_class("dash-health-value")
        self.dash_health_lbl.add_css_class("health-good")
        self.dash_health_lbl.set_xalign(0)
        health_card.append(hl)
        health_card.append(self.dash_health_lbl)
        cards_row.append(health_card)

        self.dash_grid = Gtk4.Grid()
        self.dash_grid.set_column_spacing(8)
        self.dash_grid.set_row_spacing(8)
        cards_row.append(self.dash_grid)
        cards_row.set_hexpand(True)
        box.append(cards_row)

        def add_card(title, value, row, col):
            c = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=2)
            c.add_css_class("card")
            c.add_css_class("dash-card")
            tl = Gtk4.Label(label=title)
            tl.add_css_class("dash-card-title")
            tl.set_xalign(0)
            vl = Gtk4.Label(label=value)
            vl.add_css_class("dash-card-value")
            vl.set_xalign(0)
            c.append(tl)
            c.append(vl)
            self.dash_grid.attach(c, col, row, 1, 1)
            return vl

        self.dash_os_lbl = add_card("OS", DISTRO_INFO["name"], 0, 0)
        self.dash_pkgs_lbl = add_card("Installed", "...", 0, 1)
        self.dash_upd_lbl = add_card("Updates", "...", 0, 2)
        self.dash_sec_lbl = add_card("Security", "...", 1, 0)
        self.dash_cache_lbl = add_card("Cache", "...", 1, 1)
        self.dash_kernel_lbl = add_card("Kernel", subprocess.getoutput("uname -r"), 1, 2)

        actions = Gtk4.Box(spacing=8)
        actions.add_css_class("dash-actions")
        for label, page in [("Updates", "updates"), ("Search", "install"), (".deb", "deb"), ("Security", "security")]:
            b = Gtk4.Button(label=label)
            if page == "security":
                b.add_css_class("suggested-action")
            b.connect("clicked", lambda _x, p=page: self._switch_page(p))
            actions.append(b)
        box.append(actions)

        self.dash_stats_lbl = Gtk4.Label(label="")
        self.dash_stats_lbl.add_css_class("dash-stats-line")
        self.dash_stats_lbl.set_xalign(0)
        box.append(self.dash_stats_lbl)
        return scroll

    def _create_install_page(self):
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        search_box = Gtk4.Box(spacing=10)
        self.search_entry = Gtk4.Entry(placeholder_text="Search APT, Flatpak, Snap simultaneously…")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._on_search_clicked)
        self.search_entry.connect("changed", self._on_search_changed)
        search_box.append(self.search_entry)

        self.search_spinner = Gtk4.Spinner()
        search_box.append(self.search_spinner)

        search_btn = Gtk4.Button(label="Search")
        search_btn.add_css_class("suggested-action")
        search_btn.connect("clicked", self._on_search_clicked)
        search_box.append(search_btn)
        box.append(search_box)

        filter_box = Gtk4.Box(spacing=8)
        self.filter_apt = Gtk4.ToggleButton(label="APT", active=True)
        self.filter_flatpak = Gtk4.ToggleButton(label="Flatpak", active=True)
        self.filter_snap = Gtk4.ToggleButton(label="Snap", active=True)
        for w in (self.filter_apt, self.filter_flatpak, self.filter_snap):
            filter_box.append(w)
        box.append(filter_box)

        self.install_list = Gtk4.ListBox()
        self.install_list.set_selection_mode(Gtk4.SelectionMode.NONE)
        scroll = Gtk4.ScrolledWindow()
        scroll.set_child(self.install_list)
        scroll.set_vexpand(True)
        box.append(scroll)
        return box

    def _create_deb_page(self):
        scroll = Gtk4.ScrolledWindow()
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(14)
        box.set_margin_end(14)
        scroll.set_child(box)

        toolbar = Gtk4.Box(spacing=8)
        open_btn = Gtk4.Button(label="Open .deb…")
        open_btn.set_icon_name("document-open-symbolic")
        open_btn.add_css_class("suggested-action")
        open_btn.connect("clicked", self._on_open_deb)
        toolbar.append(open_btn)

        analyze_btn = Gtk4.Button(label="Analyze")
        analyze_btn.set_icon_name("system-search-symbolic")
        analyze_btn.connect("clicked", lambda _x: self._analyze_current_deb())
        toolbar.append(analyze_btn)

        self.deb_path_entry = Gtk4.Entry(placeholder_text="Select a .deb file to analyze…")
        self.deb_path_entry.set_hexpand(True)
        self.deb_path_entry.connect("activate", lambda _e: self._analyze_current_deb())
        toolbar.append(self.deb_path_entry)
        box.append(toolbar)

        self.deb_spinner = Gtk4.Spinner()
        self.deb_spinner.set_halign(Gtk4.Align.CENTER)
        box.append(self.deb_spinner)

        self.deb_summary_lbl = Gtk4.Label(label="Choose a Debian package (.deb) to inspect dependencies.")
        self.deb_summary_lbl.set_xalign(0)
        self.deb_summary_lbl.set_wrap(True)
        box.append(self.deb_summary_lbl)

        self.deb_meta_grid = Gtk4.Grid()
        self.deb_meta_grid.set_column_spacing(16)
        self.deb_meta_grid.set_row_spacing(6)
        self.deb_meta_grid.set_margin_top(4)
        box.append(self.deb_meta_grid)

        deps_title = Gtk4.Label(label="Dependencies & Requirements")
        deps_title.add_css_class("section-title")
        deps_title.set_xalign(0)
        box.append(deps_title)

        self.deb_deps_list = Gtk4.ListBox()
        self.deb_deps_list.set_selection_mode(Gtk4.SelectionMode.NONE)
        deps_scroll = Gtk4.ScrolledWindow()
        deps_scroll.set_min_content_height(120)
        deps_scroll.set_child(self.deb_deps_list)
        box.append(deps_scroll)

        rec_title = Gtk4.Label(label="Recommended")
        rec_title.add_css_class("dim-label")
        rec_title.set_xalign(0)
        box.append(rec_title)

        self.deb_rec_list = Gtk4.ListBox()
        self.deb_rec_list.set_selection_mode(Gtk4.SelectionMode.NONE)
        rec_scroll = Gtk4.ScrolledWindow()
        rec_scroll.set_min_content_height(60)
        rec_scroll.set_child(self.deb_rec_list)
        box.append(rec_scroll)

        self.deb_simulate_buf = Gtk4.TextBuffer()
        self.deb_simulate_view = Gtk4.TextView(buffer=self.deb_simulate_buf)
        self.deb_simulate_view.set_editable(False)
        self.deb_simulate_view.set_monospace(True)
        self.deb_simulate_view.add_css_class("terminal-view")
        sim_scroll = Gtk4.ScrolledWindow()
        sim_scroll.set_min_content_height(80)
        sim_scroll.set_child(self.deb_simulate_view)
        box.append(sim_scroll)

        actions = Gtk4.Box(spacing=10)
        self.deb_install_btn = Gtk4.Button(label="Install Package")
        self.deb_install_btn.add_css_class("suggested-action")
        self.deb_install_btn.set_sensitive(False)
        self.deb_install_btn.connect("clicked", lambda _x: self._install_deb())
        actions.append(self.deb_install_btn)

        self.deb_fix_btn = Gtk4.Button(label="Fix Dependencies")
        self.deb_fix_btn.set_sensitive(False)
        self.deb_fix_btn.connect("clicked", lambda _x: self._run_privileged_command("apt-get --fix-broken install -y", label="Fix dependencies"))
        actions.append(self.deb_fix_btn)
        box.append(actions)

        hint = Gtk4.Label(label="Smart install uses APT to resolve all dependencies automatically.")
        hint.add_css_class("deb-drop-hint")
        box.append(hint)
        return scroll

    def _create_installed_page(self):
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        toolbar = Gtk4.Box(spacing=10)
        self.installed_filter = Gtk4.Entry(placeholder_text="Filter installed packages…")
        self.installed_filter.set_hexpand(True)
        self.installed_filter.connect("changed", lambda _w: self._fill_installed_list())
        toolbar.append(self.installed_filter)
        box.append(toolbar)

        self.installed_list = Gtk4.ListBox()
        scroll = Gtk4.ScrolledWindow()
        scroll.set_child(self.installed_list)
        scroll.set_vexpand(True)
        box.append(scroll)
        return box

    def _create_updates_page(self):
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        toolbar = Gtk4.Box(spacing=10)
        refresh_btn = Gtk4.Button(label="Check for Updates")
        refresh_btn.connect("clicked", lambda _x: self._load_updates(True))
        toolbar.append(refresh_btn)
        upgrade_btn = Gtk4.Button(label="Upgrade All")
        upgrade_btn.add_css_class("suggested-action")
        upgrade_btn.connect("clicked", lambda _x: self._quick_action("upgrade"))
        toolbar.append(upgrade_btn)
        sec_btn = Gtk4.Button(label="Security Only")
        sec_btn.connect("clicked", lambda _x: self._upgrade_security_only())
        toolbar.append(sec_btn)
        box.append(toolbar)

        self.updates_summary = Gtk4.Label(label="", xalign=0)
        self.updates_summary.add_css_class("dim-label")
        box.append(self.updates_summary)

        self.updates_list = Gtk4.ListBox()
        scroll = Gtk4.ScrolledWindow()
        scroll.set_child(self.updates_list)
        scroll.set_vexpand(True)
        box.append(scroll)
        return box

    def _create_security_page(self):
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=15)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)

        title = Gtk4.Label(label="Security Intelligence")
        title.add_css_class("title-2")
        title.set_xalign(0)
        box.append(title)

        self.security_summary = Gtk4.Label(label="Analyzing…", xalign=0)
        self.security_summary.add_css_class("dim-label")
        box.append(self.security_summary)

        self.security_list = Gtk4.ListBox()
        scroll = Gtk4.ScrolledWindow()
        scroll.set_child(self.security_list)
        scroll.set_vexpand(True)
        box.append(scroll)

        btn_row = Gtk4.Box(spacing=10)
        b1 = Gtk4.Button(label="Install Security Updates")
        b1.add_css_class("suggested-action")
        b1.connect("clicked", lambda _x: self._upgrade_security_only())
        btn_row.append(b1)
        b2 = Gtk4.Button(label="Audit Held Packages")
        b2.connect("clicked", lambda _x: self._run_privileged_command("apt-mark showhold"))
        btn_row.append(b2)
        box.append(btn_row)
        return box

    def _create_repos_page(self):
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        title = Gtk4.Label(label="PPA & Software Sources")
        title.add_css_class("section-title")
        title.set_xalign(0)
        box.append(title)

        ppa_box = Gtk4.Box(spacing=10)
        self.ppa_entry = Gtk4.Entry(placeholder_text="ppa:user/repository")
        self.ppa_entry.set_hexpand(True)
        ppa_box.append(self.ppa_entry)
        add_ppa_btn = Gtk4.Button(label="Add PPA")
        add_ppa_btn.connect("clicked", self._on_add_ppa)
        ppa_box.append(add_ppa_btn)
        box.append(ppa_box)

        self.repos_list = Gtk4.ListBox()
        scroll = Gtk4.ScrolledWindow()
        scroll.set_child(self.repos_list)
        scroll.set_vexpand(True)
        box.append(scroll)
        return box

    def _create_appimages_page(self):
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        toolbar = Gtk4.Box(spacing=10)
        scan_btn = Gtk4.Button(label="Scan Home Directory")
        scan_btn.connect("clicked", lambda _x: self._load_appimages())
        toolbar.append(scan_btn)
        open_btn = Gtk4.Button(label="Open AppImage…")
        open_btn.connect("clicked", self._on_open_appimage)
        toolbar.append(open_btn)
        box.append(toolbar)

        self.appimages_list = Gtk4.ListBox()
        scroll = Gtk4.ScrolledWindow()
        scroll.set_child(self.appimages_list)
        scroll.set_vexpand(True)
        box.append(scroll)
        return box

    def _create_system_page(self):
        scroll = Gtk4.ScrolledWindow()
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        scroll.set_child(box)

        def add_section(title, buttons):
            lbl = Gtk4.Label(label=title)
            lbl.add_css_class("section-title")
            lbl.set_xalign(0)
            box.append(lbl)
            grid = Gtk4.Grid()
            grid.set_column_spacing(10)
            grid.set_row_spacing(10)
            grid.set_column_homogeneous(True)
            for i, (l, c) in enumerate(buttons):
                b = Gtk4.Button(label=l)
                b.connect("clicked", lambda _x, cmd=c: self._run_privileged_command(cmd))
                grid.attach(b, i % 3, i // 3, 1, 1)
            box.append(grid)

        add_section("Maintenance", [
            ("APT Update", "apt update"),
            ("APT Upgrade", "apt upgrade -y"),
            ("Autoremove", "apt autoremove -y"),
            ("Clean Cache", "apt clean"),
            ("Fix Broken", "apt --fix-broken install -y"),
            ("Configure All", "dpkg --configure -a"),
            ("Dist Upgrade", "apt dist-upgrade -y"),
            ("Remove Orphans", "deborphan -a | xargs apt remove -y 2>/dev/null || true"),
        ])
        add_section("Diagnostics", [
            ("System Info", "uname -a && lsb_release -a 2>/dev/null"),
            ("Disk Usage", "df -h"),
            ("Memory Info", "free -h"),
            ("Failed Services", "systemctl --failed --no-pager"),
            ("Journal Errors", "journalctl -p 3 -xb --no-pager | tail -50"),
            ("Network Check", "ping -c 3 8.8.8.8"),
            ("GPU Info", "lspci | grep -i vga"),
            ("CPU Info", "lscpu | head -20"),
        ])
        add_section("Smart Tools", [
            ("List Large Packages", "dpkg-query -Wf '${Installed-Size}\\t${Package}\\n' | sort -n | tail -20"),
            ("Check Broken Deps", "apt-get check"),
            ("Verify Packages", "debsums -c 2>/dev/null | head -30 || echo 'debsums not installed'"),
            ("Kernel List", "dpkg --list | grep linux-image"),
        ])
        return scroll

    def _create_settings_page(self):
        scroll = Gtk4.ScrolledWindow()
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)
        scroll.set_child(box)

        title = Gtk4.Label(label="Settings")
        title.add_css_class("title-2")
        title.set_xalign(0)
        box.append(title)

        self.setting_auto_check = Gtk4.CheckButton(label="Auto-check updates on startup")
        self.setting_auto_check.set_active(self.settings.get("auto_check_updates", True))
        box.append(self.setting_auto_check)

        self.setting_security = Gtk4.CheckButton(label="Show security alerts prominently")
        self.setting_security.set_active(self.settings.get("show_security_alerts", True))
        box.append(self.setting_security)

        self.setting_confirm_install = Gtk4.CheckButton(label="Confirm before installing packages")
        self.setting_confirm_install.set_active(self.settings.get("confirm_install", True))
        box.append(self.setting_confirm_install)

        self.setting_confirm_remove = Gtk4.CheckButton(label="Confirm before removing packages")
        self.setting_confirm_remove.set_active(self.settings.get("confirm_remove", True))
        box.append(self.setting_confirm_remove)

        src_lbl = Gtk4.Label(label="Search Sources", xalign=0)
        src_lbl.add_css_class("section-title")
        box.append(src_lbl)
        self.setting_search_apt = Gtk4.CheckButton(label="Include APT")
        self.setting_search_apt.set_active(self.settings.get("search_apt", True))
        box.append(self.setting_search_apt)
        self.setting_search_flatpak = Gtk4.CheckButton(label="Include Flatpak")
        self.setting_search_flatpak.set_active(self.settings.get("search_flatpak", True))
        box.append(self.setting_search_flatpak)
        self.setting_search_snap = Gtk4.CheckButton(label="Include Snap")
        self.setting_search_snap.set_active(self.settings.get("search_snap", True))
        box.append(self.setting_search_snap)

        save_btn = Gtk4.Button(label="Save Settings")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._save_settings)
        box.append(save_btn)
        return scroll

    def _create_logs_page(self):
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=5)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)

        self.log_buffer = Gtk4.TextBuffer()
        self.log_view = Gtk4.TextView(buffer=self.log_buffer)
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.add_css_class("terminal-view")

        scroll = Gtk4.ScrolledWindow()
        scroll.set_child(self.log_view)
        scroll.set_vexpand(True)
        box.append(scroll)

        btn_row = Gtk4.Box(spacing=10)
        clear_btn = Gtk4.Button(label="Clear")
        clear_btn.connect("clicked", lambda _x: self.log_buffer.set_text(""))
        btn_row.append(clear_btn)
        export_btn = Gtk4.Button(label="Export Log")
        export_btn.connect("clicked", self._export_log)
        btn_row.append(export_btn)
        box.append(btn_row)
        return box

    def _create_news_page(self):
        box = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        toolbar = Gtk4.Box(spacing=10)
        lbl = Gtk4.Label(label=f"News — {DISTRO_INFO['name']}")
        lbl.add_css_class("section-title")
        toolbar.append(lbl)
        refresh = Gtk4.Button(label="Refresh")
        refresh.connect("clicked", lambda _x: self._load_news())
        toolbar.append(refresh)
        box.append(toolbar)

        self.news_buffer = Gtk4.TextBuffer()
        self.news_view = Gtk4.TextView(buffer=self.news_buffer)
        self.news_view.set_editable(False)
        self.news_view.set_wrap_mode(Gtk4.WrapMode.WORD)

        scroll = Gtk4.ScrolledWindow()
        scroll.set_child(self.news_view)
        scroll.set_vexpand(True)
        box.append(scroll)
        return box

    # --- Navigation & State ---
    def _switch_page(self, name):
        titles = {
            "dash": "Home", "install": "Install", "deb": "DEB Install",
            "installed": "Installed", "updates": "Updates", "security": "Security",
            "repos": "Sources", "appimages": "AppImages", "system": "Tools",
            "settings": "Settings", "logs": "Logs", "news": "News",
        }
        self.stack.set_visible_child_name(name)
        self.page_title_lbl.set_label(titles.get(name, name.capitalize()))
        for k, v in self.nav_btns.items():
            if k == name:
                v.add_css_class("active")
            else:
                v.remove_css_class("active")
        if name == "security":
            self._fill_security_page()

    def _refresh_current_page(self):
        page = self.stack.get_visible_child_name()
        if page == "updates":
            self._load_updates(True)
        elif page == "installed":
            self._reload_installed()
        elif page == "repos":
            self._load_repos_list()
        elif page == "appimages":
            self._load_appimages()
        elif page == "deb" and self.deb_file_path:
            self._analyze_current_deb()
        elif page == "news":
            self._load_news()
        else:
            self._load_initial_data()

    def _set_status(self, text):
        GLib.idle_add(self.status_lbl.set_label, text)

    def _set_progress(self, val, text=""):
        def apply():
            self.progress_bar.set_fraction(max(0, min(1, val / 100.0)))
            if text:
                self.progress_bar.set_text(text)
        GLib.idle_add(apply)

    def _toast(self, message, timeout=3):
        if Adw and self.toast_overlay:
            toast = Adw.Toast.new(message)
            toast.set_timeout(timeout)
            self.toast_overlay.add_toast(toast)

    def _append_output(self, text):
        def apply():
            self.log_buffer.insert(self.log_buffer.get_end_iter(), text + "\n")
            mark = self.log_buffer.get_insert()
            self.log_view.scroll_to_mark(mark, 0.0, True, 0.5, 1.0)
        GLib.idle_add(apply)

    def _record_history(self, label, cmd):
        entry = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "label": label, "cmd": cmd}
        self.command_history.append(entry)
        if len(self.command_history) > 200:
            self.command_history = self.command_history[-200:]
        AppConfig.save_json(HISTORY_FILE, self.command_history)

    # --- Data Loading ---
    def _load_initial_data(self):
        self._set_status("Initializing…")
        self._set_progress(10, "Loading")

        def worker():
            self._set_progress(20, "Packages")
            self.installed_pkgs = fetch_installed_packages()
            self._set_progress(40, "System")
            self.system_stats = get_system_stats()
            self.held_packages = fetch_held_packages()
            GLib.idle_add(self._update_dash)
            self._set_progress(60, "Updates")
            if self.settings.get("auto_check_updates", True):
                self._load_updates(False, sync_dash=False)
            self._set_progress(80, "News")
            self._load_news()
            self._load_appimages()
            self._load_repos_list()
            GLib.idle_add(lambda: self._fill_installed_list())
            self._set_progress(100, "")
            self._set_status("Ready")
            sec_count = sum(1 for u in self.updates_data if u.get("severity") == "security")
            if sec_count and self.settings.get("show_security_alerts", True):
                GLib.idle_add(lambda: self._toast(f"⚠ {sec_count} security update(s) available!", 5))

        threading.Thread(target=worker, daemon=True).start()

    def _update_dash(self):
        score = compute_health_score(
            len(self.installed_pkgs), self.updates_data, self.held_packages, self.system_stats,
        )
        self.dash_health_lbl.set_label(f"{score}%")
        for cls in ("health-excellent", "health-good", "health-warning", "health-critical"):
            self.dash_health_lbl.remove_css_class(cls)
        if score >= 90:
            self.dash_health_lbl.add_css_class("health-excellent")
        elif score >= 70:
            self.dash_health_lbl.add_css_class("health-good")
        elif score >= 50:
            self.dash_health_lbl.add_css_class("health-warning")
        else:
            self.dash_health_lbl.add_css_class("health-critical")

        self.dash_pkgs_lbl.set_label(str(len(self.installed_pkgs)))
        self.dash_upd_lbl.set_label(str(len(self.updates_data)))
        sec = sum(1 for u in self.updates_data if u.get("severity") == "security")
        self.dash_sec_lbl.set_label(str(sec))
        cache = self.system_stats.get("cache_size", 0)
        self.dash_cache_lbl.set_label(human_size(cache))
        stats = self.system_stats
        self.dash_stats_lbl.set_label(
            f"Uptime: {stats.get('uptime', '-')}  ·  Load: {stats.get('load', '-')}  ·  "
            f"Disk free: {stats.get('disk_free', '-')}  ·  RAM used: {stats.get('mem_used_pct', '-')}",
        )

    def _load_updates(self, force=False, sync_dash=True):
        def worker():
            if force:
                self._set_status("Updating APT cache…")
                run_command(["pkexec", "apt", "update"], timeout=180)
            self._set_status("Checking for updates…")
            self.updates_data = fetch_all_pending_updates(False)
            GLib.idle_add(self._fill_updates_list)
            if sync_dash:
                GLib.idle_add(self._update_dash)
            self._set_status("Ready")
        threading.Thread(target=worker, daemon=True).start()

    def _reload_installed(self):
        def worker():
            self.installed_pkgs = fetch_installed_packages()
            GLib.idle_add(self._fill_installed_list)
            GLib.idle_add(self._update_dash)
        threading.Thread(target=worker, daemon=True).start()

    def _fill_updates_list(self):
        self._clear_list(self.updates_list)
        if not self.updates_data:
            self.updates_list.append(Gtk4.Label(label="✓ System is up to date"))
            self.updates_summary.set_label("")
            return

        sec = sum(1 for u in self.updates_data if u.get("severity") == "security")
        kern = sum(1 for u in self.updates_data if u.get("severity") == "kernel")
        self.updates_summary.set_label(
            f"{len(self.updates_data)} updates  ·  {sec} security  ·  {kern} kernel",
        )

        for u in sorted(self.updates_data, key=lambda x: {"security": 0, "kernel": 1, "important": 2, "normal": 3}.get(x.get("severity"), 4)):
            row = Gtk4.Box(orientation=Gtk4.Orientation.HORIZONTAL, spacing=12)
            row.set_margin_top(8)
            row.set_margin_bottom(8)
            row.set_margin_start(10)
            row.set_margin_end(10)
            row.add_css_class("card")

            info = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL)
            info.set_hexpand(True)
            name_lbl = Gtk4.Label(label=f"{u['name']}  [{u['source'].upper()}]", xalign=0)
            name_lbl.add_css_class("section-title")
            info.append(name_lbl)
            info.append(Gtk4.Label(label=f"{u['from']} → {u['to']}", xalign=0, css_classes=["dim-label"]))

            sev = u.get("severity", "normal")
            badge = Gtk4.Label(label=SEVERITY_LABELS.get(sev, "Update"))
            badge.add_css_class({"security": "badge-security", "kernel": "badge-kernel"}.get(sev, "badge-normal"))
            info.append(badge)
            row.append(info)

            up_btn = Gtk4.Button(label="Upgrade")
            up_btn.connect("clicked", lambda _x, pkg=u: self._upgrade_single(pkg))
            row.append(up_btn)
            self.updates_list.append(row)

    def _fill_security_page(self):
        self._clear_list(self.security_list)
        security_updates = [u for u in self.updates_data if u.get("severity") in ("security", "kernel")]
        if not security_updates:
            self.security_summary.set_label("✓ No pending security or kernel updates detected.")
            self.security_list.append(Gtk4.Label(label="Your system appears secure."))
            return

        self.security_summary.set_label(f"⚠ {len(security_updates)} critical update(s) require attention.")
        for u in security_updates:
            row = Gtk4.Box(spacing=10)
            row.set_margin_top(8)
            row.set_margin_bottom(8)
            row.add_css_class("card")
            row.append(Gtk4.Label(label=f"{u['name']}: {u['from']} → {u['to']} [{u.get('severity', '').upper()}]", xalign=0))
            self.security_list.append(row)

        if self.held_packages:
            sep = Gtk4.Label(label=f"\n{len(self.held_packages)} package(s) are held back:", xalign=0)
            sep.add_css_class("dim-label")
            self.security_list.append(sep)
            for h in self.held_packages[:10]:
                self.security_list.append(Gtk4.Label(label=f"  • {h['name']}", xalign=0))

    def _fill_installed_list(self):
        self._clear_list(self.installed_list)
        filt = (self.installed_filter.get_text() or "").lower()
        shown = 0
        for p in self.installed_pkgs:
            if filt and filt not in p["name"].lower() and filt not in p.get("description", "").lower():
                continue
            shown += 1
            if shown > 500:
                break
            row = Gtk4.Box(orientation=Gtk4.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_top(6)
            row.set_margin_bottom(6)
            row.set_margin_start(8)
            row.set_margin_end(8)

            info = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL)
            info.set_hexpand(True)
            info.append(Gtk4.Label(label=p["name"], xalign=0, css_classes=["bold"]))
            desc = p.get("description", "")[:80]
            info.append(Gtk4.Label(label=f"v{p['version']} — {desc}", xalign=0, css_classes=["dim-label"]))
            row.append(info)

            info_btn = Gtk4.Button(icon_name="dialog-information-symbolic")
            info_btn.set_tooltip_text("Details")
            info_btn.connect("clicked", lambda _x, pkg=p: self._show_package_info(pkg["name"], "apt"))
            row.append(info_btn)

            rm_btn = Gtk4.Button(label="Remove")
            rm_btn.add_css_class("destructive-action")
            rm_btn.connect("clicked", lambda _x, pkg=p["name"]: self._remove_package(pkg, "apt"))
            row.append(rm_btn)
            self.installed_list.append(row)

    def _load_news(self):
        def worker():
            try:
                req = urllib.request.Request(NEWS_RSS, headers={"User-Agent": f"PKGER-D/{APP_VERSION}"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    root = ET.fromstring(r.read())
                    txt = ""
                    for item in root.findall(".//item")[:20]:
                        title = item.find("title")
                        link = item.find("link")
                        pub = item.find("pubDate")
                        if title is not None:
                            txt += f"▸ {title.text}\n"
                            if pub is not None:
                                txt += f"  {pub.text}\n"
                            if link is not None:
                                txt += f"  {link.text}\n\n"
                    GLib.idle_add(self.news_buffer.set_text, txt or "No news available.")
            except Exception as e:
                GLib.idle_add(self.news_buffer.set_text, f"Could not load news: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _load_appimages(self):
        def worker():
            apps = scan_appimages()
            def apply():
                self._clear_list(self.appimages_list)
                for a in apps:
                    row = Gtk4.Box(spacing=10)
                    row.set_margin_top(6)
                    row.set_margin_bottom(6)
                    row.add_css_class("card")
                    info = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL)
                    info.set_hexpand(True)
                    info.append(Gtk4.Label(label=a["name"], xalign=0, css_classes=["bold"]))
                    info.append(Gtk4.Label(label=f"{a['version']} — {a['path']}", xalign=0, css_classes=["dim-label"]))
                    row.append(info)
                    run_btn = Gtk4.Button(label="Run")
                    run_btn.connect("clicked", lambda _x, path=a["path"]: self._run_appimage(path))
                    row.append(run_btn)
                    self.appimages_list.append(row)
                self._toast(f"Found {len(apps)} AppImage(s)")
            GLib.idle_add(apply)
        threading.Thread(target=worker, daemon=True).start()

    def _load_repos_list(self):
        def worker():
            repos = list_ppa_sources()
            def apply():
                self._clear_list(self.repos_list)
                for r in repos:
                    row = Gtk4.Box(spacing=10)
                    row.set_margin_top(6)
                    row.set_margin_bottom(6)
                    row.add_css_class("card")
                    type_lbl = Gtk4.Label(label=r["type"].upper())
                    type_lbl.add_css_class("badge-normal")
                    row.append(type_lbl)
                    info = Gtk4.Label(label=f"{r['name']}  ({Path(r['file']).name})", xalign=0)
                    info.set_hexpand(True)
                    row.append(info)
                    if r["type"] == "ppa":
                        rm = Gtk4.Button(label="Remove")
                        rm.connect("clicked", lambda _x, name=r["name"]: self._remove_ppa(name))
                        row.append(rm)
                    self.repos_list.append(row)
            GLib.idle_add(apply)
        threading.Thread(target=worker, daemon=True).start()

    def _clear_list(self, listbox):
        c = listbox.get_first_child()
        while c:
            listbox.remove(c)
            c = listbox.get_first_child()

    # --- Search & Install ---
    def _on_search_changed(self, _entry):
        if self._search_timer:
            GLib.source_remove(self._search_timer)
        self._search_timer = GLib.timeout_add(400, self._debounced_search)

    def _debounced_search(self):
        self._search_timer = None
        q = self.search_entry.get_text().strip()
        if len(q) >= 2:
            self._on_search_clicked(None)
        return False

    def _on_search_clicked(self, _w):
        q = self.search_entry.get_text().strip()
        if not q:
            return
        self._switch_page("install")
        self._set_status(f"Searching: {q}")
        self.search_spinner.start()

        def worker():
            s = dict(self.settings)
            s["search_apt"] = s.get("search_apt", True) and self.filter_apt.get_active()
            s["search_flatpak"] = s.get("search_flatpak", True) and self.filter_flatpak.get_active()
            s["search_snap"] = s.get("search_snap", True) and self.filter_snap.get_active()
            results = parallel_search(q, s)
            GLib.idle_add(self._fill_install_results, results)
            GLib.idle_add(self.search_spinner.stop)
        threading.Thread(target=worker, daemon=True).start()

    def _fill_install_results(self, res):
        self._clear_list(self.install_list)
        for r in res:
            row = Gtk4.Box(spacing=10)
            row.set_margin_top(8)
            row.set_margin_bottom(8)
            row.set_margin_start(10)
            row.set_margin_end(10)
            row.add_css_class("card")

            info = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL)
            info.set_hexpand(True)
            src = r["source"].upper()
            info.append(Gtk4.Label(label=f"{r['name']}  [{src}]", xalign=0, css_classes=["bold"]))
            info.append(Gtk4.Label(label=r.get("desc", "")[:120], xalign=0, css_classes=["dim-label"]))
            row.append(info)

            info_btn = Gtk4.Button(icon_name="dialog-information-symbolic")
            info_btn.connect("clicked", lambda _x, pkg=r["name"], src=r["source"]: self._show_package_info(pkg, src))
            row.append(info_btn)

            fav_btn = Gtk4.Button(icon_name="starred-symbolic" if r["name"] in self.favorites else "star-new-symbolic")
            fav_btn.connect("clicked", lambda _x, pkg=r["name"], btn=fav_btn: self._toggle_favorite(pkg, btn))
            row.append(fav_btn)

            btn = Gtk4.Button(label="Install")
            btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda _x, pkg=r["name"], src=r["source"]: self._install_package(pkg, src))
            row.append(btn)
            self.install_list.append(row)
        self._set_status(f"Found {len(res)} result(s)")

    def _toggle_favorite(self, pkg, btn):
        if pkg in self.favorites:
            self.favorites.discard(pkg)
            btn.set_icon_name("star-new-symbolic")
        else:
            self.favorites.add(pkg)
            btn.set_icon_name("starred-symbolic")
        AppConfig.save_json(FAVORITES_FILE, list(self.favorites))

    def _show_package_info(self, name, source):
        info = fetch_package_info(name, source)
        dialog = Gtk4.Dialog(title=f"Package: {name}", transient_for=self, modal=True)
        dialog.set_default_size(520, 400)
        area = dialog.get_content_area()
        area.set_margin_top(16)
        area.set_margin_bottom(16)
        area.set_margin_start(16)
        area.set_margin_end(16)

        grid = Gtk4.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)
        rows = [
            ("Name", info["name"]), ("Source", source.upper()),
            ("Version", info.get("version", "-")), ("Size", info.get("size", "-")),
            ("Description", info.get("description", "-")),
        ]
        for i, (k, v) in enumerate(rows):
            grid.attach(Gtk4.Label(label=k, xalign=0, css_classes=["bold"]), 0, i, 1, 1)
            grid.attach(Gtk4.Label(label=str(v), xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD), 1, i, 1, 1)
        area.append(grid)

        if info.get("depends"):
            deps = Gtk4.Label(label="Dependencies: " + ", ".join(info["depends"]), xalign=0, wrap=True)
            deps.add_css_class("dim-label")
            area.append(deps)

        close_btn = dialog.add_button("Close", Gtk4.ResponseType.CLOSE)
        close_btn.connect("clicked", lambda _x: dialog.close())
        dialog.present()

    def _confirm(self, title, message, on_result):
        """
        GTK4-safe confirmation dialog.
        Calls on_result(True/False) asynchronously.
        """
        dialog = Gtk4.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk4.MessageType.QUESTION,
            buttons=Gtk4.ButtonsType.YES_NO,
            text=title,
            secondary_text=message,
        )

        def on_response(d, r):
            try:
                d.close()
            except Exception:
                try:
                    d.destroy()
                except Exception:
                    pass
            on_result(r == Gtk4.ResponseType.YES)

        dialog.connect("response", on_response)
        dialog.present()

    def _install_package(self, pkg, source):
        if source == "apt":
            cmd = f"apt install -y {shlex.quote(pkg)}"
        elif source == "flatpak":
            cmd = f"flatpak install -y {shlex.quote(pkg)}"
        else:
            cmd = f"snap install {shlex.quote(pkg)}"
        if not self.settings.get("confirm_install", True):
            self._run_privileged_command(cmd, label=f"Install {pkg}")
            return

        self._confirm(
            "Confirm Install",
            f"Install {pkg} from {source.upper()}?",
            lambda ok: self._run_privileged_command(cmd, label=f"Install {pkg}") if ok else None,
        )

    def _remove_package(self, pkg, source):
        if source == "apt":
            cmd = f"apt remove -y {shlex.quote(pkg)}"
        elif source == "flatpak":
            cmd = f"flatpak uninstall -y {shlex.quote(pkg)}"
        else:
            cmd = f"snap remove {shlex.quote(pkg)}"
        if not self.settings.get("confirm_remove", True):
            self._run_privileged_command(cmd, label=f"Remove {pkg}")
            return

        self._confirm(
            "Confirm Remove",
            f"Remove {pkg}?",
            lambda ok: self._run_privileged_command(cmd, label=f"Remove {pkg}") if ok else None,
        )

    def _upgrade_single(self, update):
        pkg, src = update["pkg"], update["source"]
        if src == "apt":
            cmd = f"apt install -y {shlex.quote(pkg)}"
        elif src == "flatpak":
            cmd = f"flatpak update -y {shlex.quote(pkg)}"
        else:
            cmd = f"snap refresh {shlex.quote(pkg)}"
        self._run_privileged_command(cmd, label=f"Upgrade {pkg}")

    def _upgrade_security_only(self):
        security = [u for u in self.updates_data if u.get("severity") in ("security", "kernel")]
        if not security:
            self._toast("No security updates pending")
            return
        names = " ".join(shlex.quote(u["pkg"]) for u in security if u["source"] == "apt")
        if names:
            self._run_privileged_command(f"apt install -y {names}", label="Security updates")

    def _on_add_ppa(self, _b):
        ppa = self.ppa_entry.get_text().strip()
        if ppa:
            self._run_privileged_command(f"add-apt-repository -y {shlex.quote(ppa)} && apt update", label=f"Add PPA {ppa}")

    def _remove_ppa(self, name):
        self._run_privileged_command(f"add-apt-repository -y -r ppa:{name.replace('ppa-', '').replace('_', '/')}", label=f"Remove PPA {name}")

    def _quick_action(self, act):
        cmds = {
            "upgrade": "apt update && apt upgrade -y",
            "clean": "apt clean && apt autoremove -y",
            "fix": "apt --fix-broken install -y",
        }
        labels = {"upgrade": "Upgrade All", "clean": "Clean Cache", "fix": "Fix Broken"}
        if act in cmds:
            self._run_privileged_command(cmds[act], label=labels[act])

    def _run_privileged_command(self, cmd, label=None):
        label = label or cmd
        self._switch_page("logs")
        self._record_history(label, cmd)
        self.log_buffer.set_text(f"$ {cmd}\n{'=' * 50}\n")
        self._set_progress(0, "Running…")

        def worker():
            try:
                p = subprocess.Popen(
                    ["pkexec", "bash", "-c", cmd],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                lines = 0
                for line in p.stdout:
                    lines += 1
                    GLib.idle_add(self._append_output, line.rstrip())
                    if lines % 5 == 0:
                        self._set_progress(min(90, lines), "Running…")
                code = p.wait()
                GLib.idle_add(self._append_output, f"\n{'=' * 50}\nExit code: {code}")
                self._set_progress(100 if code == 0 else 0, "Done" if code == 0 else "Failed")
                if code == 0:
                    GLib.idle_add(lambda: self._toast(f"✓ {label} completed"))
                else:
                    GLib.idle_add(lambda: self._toast(f"✗ {label} failed"))
                GLib.idle_add(self._load_initial_data)
            except Exception as e:
                GLib.idle_add(self._append_output, f"ERROR: {e}")
                self._set_progress(0, "Error")
        threading.Thread(target=worker, daemon=True).start()

    def _run_appimage(self, path):
        try:
            subprocess.Popen([path], start_new_session=True)
            self._toast(f"Launched {Path(path).name}")
        except Exception as e:
            self._toast(f"Failed to launch: {e}")

    def _deb_file_filter(self):
        store = Gio4.ListStore.new(Gtk4.FileFilter)
        filt = Gtk4.FileFilter()
        filt.set_name("Debian packages")
        filt.add_pattern("*.deb")
        filt.add_mime_type("application/vnd.debian.binary-package")
        store.append(filt)
        return store

    def _on_open_deb(self, _b=None):
        dialog = Gtk4.FileDialog(title="Select Debian Package")
        dialog.set_filters(self._deb_file_filter())
        dialog.open(self, None, lambda d, r: self._on_deb_selected(d, r))

    def _on_deb_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                self.deb_path_entry.set_text(path)
                self.deb_file_path = path
                self._switch_page("deb")
                self._analyze_current_deb()
        except Exception:
            pass

    def _analyze_current_deb(self):
        path = (self.deb_path_entry.get_text() or "").strip()
        if not path:
            self._toast("Select a .deb file first")
            return
        if not path.lower().endswith(".deb"):
            self._toast("File must be a .deb package")
            return
        self.deb_file_path = path
        self.deb_spinner.start()
        self.deb_install_btn.set_sensitive(False)
        self._set_status("Analyzing .deb package…")

        def worker():
            analysis = analyze_deb_file(path)
            GLib.idle_add(self._apply_deb_analysis, analysis)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_deb_analysis(self, analysis):
        self.deb_spinner.stop()
        self.deb_analysis = analysis
        if analysis.get("error"):
            self.deb_summary_lbl.set_label(analysis["error"])
            for cls in ("deb-summary-ok", "deb-summary-warn", "deb-summary-error"):
                self.deb_summary_lbl.remove_css_class(cls)
            self.deb_summary_lbl.add_css_class("deb-summary-error")
            self._set_status("DEB analysis failed")
            return

        summary = analysis.get("summary", "")
        for cls in ("deb-summary-ok", "deb-summary-warn", "deb-summary-error"):
            self.deb_summary_lbl.remove_css_class(cls)
        if analysis.get("conflicts") or not analysis.get("arch_ok"):
            self.deb_summary_lbl.add_css_class("deb-summary-error")
        elif analysis.get("warnings"):
            self.deb_summary_lbl.add_css_class("deb-summary-warn")
        else:
            self.deb_summary_lbl.add_css_class("deb-summary-ok")
        self.deb_summary_lbl.set_label(summary)

        # Clear meta grid
        child = self.deb_meta_grid.get_first_child()
        while child:
            next_c = child.get_next_sibling()
            self.deb_meta_grid.remove(child)
            child = next_c

        meta_rows = [
            ("Package", analysis["package"]),
            ("Version", analysis["version"]),
            ("Architecture", f"{analysis['deb_arch']} / {analysis['system_arch']}"),
            ("File size", analysis["file_size"]),
            ("Installed size", analysis["installed_size"]),
            ("Maintainer", (analysis["fields"].get("Maintainer") or "-")[:60]),
        ]
        if analysis.get("installed"):
            meta_rows.append(("Current", analysis.get("installed_version") or "installed"))
        for i, (k, v) in enumerate(meta_rows):
            kl = Gtk4.Label(label=k, xalign=0)
            kl.add_css_class("deb-meta")
            vl = Gtk4.Label(label=str(v), xalign=0, wrap=True)
            vl.add_css_class("deb-meta-val")
            self.deb_meta_grid.attach(kl, 0, i, 1, 1)
            self.deb_meta_grid.attach(vl, 1, i, 1, 1)

        self._clear_list(self.deb_deps_list)
        if analysis.get("conflicts"):
            for c in analysis["conflicts"]:
                row = Gtk4.Label(
                    label=f"⚠ {c['type']}: {c['package']} ({c.get('version', '?')}) is installed",
                    xalign=0, css_classes=["deb-dep-miss"],
                )
                row.set_margin_top(4)
                row.set_margin_bottom(4)
                self.deb_deps_list.append(row)

        if analysis.get("extra_new"):
            row = Gtk4.Label(
                label=f"APT will install {len(analysis['extra_new'])} extra package(s): {', '.join(analysis['extra_new'][:8])}",
                xalign=0, css_classes=["deb-dep-ok"],
            )
            row.set_margin_top(4)
            row.set_margin_bottom(4)
            self.deb_deps_list.append(row)

        for dep in analysis.get("depends", []):
            row = Gtk4.Box(orientation=Gtk4.Orientation.HORIZONTAL, spacing=8)
            row.set_margin_top(3)
            row.set_margin_bottom(3)
            status = "✓" if dep.get("status") == "ok" else "✗"
            css = "deb-dep-ok" if dep.get("status") == "ok" else "deb-dep-miss"
            st = Gtk4.Label(label=status, css_classes=[css])
            txt = dep.get("clause", dep.get("name", ""))
            if dep.get("status") == "ok" and dep.get("version"):
                txt += f"  ({dep['version']})"
            row.append(st)
            row.append(Gtk4.Label(label=f"[{dep.get('dep_type', 'Dep')}] {txt}", xalign=0, wrap=True))
            self.deb_deps_list.append(row)

        if not analysis.get("depends") and not analysis.get("conflicts") and not analysis.get("extra_new"):
            self.deb_deps_list.append(Gtk4.Label(label="No dependencies required", xalign=0))

        self._clear_list(self.deb_rec_list)
        recs = analysis.get("recommends", [])
        if recs:
            for dep in recs[:12]:
                css = "deb-dep-ok" if dep.get("status") == "ok" else "deb-dep-miss"
                icon = "✓" if dep.get("status") == "ok" else "○"
                self.deb_rec_list.append(Gtk4.Label(label=f"{icon} {dep.get('clause', '')}", xalign=0, css_classes=[css]))
        else:
            self.deb_rec_list.append(Gtk4.Label(label="None", xalign=0, css_classes=["dim-label"]))

        desc = (analysis["fields"].get("Description") or "").replace("\n", " ")
        sim_text = f"Description: {desc}\n\n--- APT Simulation ---\n{analysis.get('simulate_output', '')}"
        self.deb_simulate_buf.set_text(sim_text[:4000])

        can_install = analysis.get("ready") or (
            analysis.get("arch_ok") and not analysis.get("conflicts") and analysis.get("simulate_ok")
        )
        self.deb_install_btn.set_sensitive(can_install)
        self.deb_fix_btn.set_sensitive(bool(analysis.get("missing_deps")))
        self._set_status(f"Analyzed {analysis['filename']}")

    def _install_deb(self):
        if not self.deb_analysis or not self.deb_file_path:
            self._toast("Analyze a .deb file first")
            return
        a = self.deb_analysis
        pkg = a.get("package", "package")
        ver = a.get("version", "")
        msg = f"Install {pkg} {ver}?\n\nAPT will resolve dependencies automatically."
        if a.get("extra_new"):
            msg += f"\n\nAdditional packages: {', '.join(a['extra_new'][:6])}"
            if len(a["extra_new"]) > 6:
                msg += f" (+{len(a['extra_new']) - 6} more)"

        if not self.settings.get("confirm_install", True):
            cmd = build_deb_install_command(self.deb_file_path)
            self._run_privileged_command(cmd, label=f"Install {pkg}")
            return

        self._confirm(
            "Install DEB Package",
            msg,
            lambda ok: self._run_privileged_command(
                build_deb_install_command(self.deb_file_path), label=f"Install {pkg}",
            ) if ok else None,
        )

    def _on_open_appimage(self, _b):
        dialog = Gtk4.FileDialog(title="Select AppImage")
        dialog.open(self, None, lambda d, r: self._on_appimage_selected(d, r))

    def _on_appimage_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                self._run_appimage(file.get_path())
        except Exception:
            pass

    def _save_settings(self):
        self.settings = {
            "auto_check_updates": self.setting_auto_check.get_active(),
            "show_security_alerts": self.setting_security.get_active(),
            "confirm_install": self.setting_confirm_install.get_active(),
            "confirm_remove": self.setting_confirm_remove.get_active(),
            "search_apt": self.setting_search_apt.get_active(),
            "search_flatpak": self.setting_search_flatpak.get_active(),
            "search_snap": self.setting_search_snap.get_active(),
        }
        AppConfig.save(self.settings)
        self._toast("Settings saved")

    def _export_log(self, _b):
        dialog = Gtk4.FileDialog(title="Export Log")
        dialog.set_initial_name(f"pkger-d-log-{datetime.now():%Y%m%d-%H%M%S}.txt")

        def on_save(_d, result):
            try:
                file = dialog.save_finish(result)
                start, end = self.log_buffer.get_bounds()
                text = self.log_buffer.get_text(start, end, False)
                with open(file.get_path(), "w") as f:
                    f.write(text)
                self._toast("Log exported")
            except Exception:
                pass

        dialog.save(self, None, on_save)

    def _show_about(self):
        dialog = Gtk4.AboutDialog()
        dialog.set_transient_for(self)
        dialog.set_program_name(APP_NAME)
        dialog.set_version(APP_VERSION)
        dialog.set_authors([APP_DEVELOPER])
        dialog.set_copyright(f"© {APP_YEAR} {APP_DEVELOPER}")
        dialog.set_comments(
            "Professional Package Manager for Debian-based systems.\n"
            "APT · Flatpak · Snap · AppImage · Security Intelligence",
        )
        dialog.set_license_type(Gtk4.License.GPL_3_0)
        dialog.set_website("https://github.com/almezali/pkger-d")
        dialog.set_logo_icon_name("system-software-install-symbolic")
        dialog.present()

    def _show_history(self):
        dialog = Gtk4.Dialog(title="Command History", transient_for=self, modal=True)
        dialog.set_default_size(640, 420)
        area = dialog.get_content_area()
        scroll = Gtk4.ScrolledWindow()
        scroll.set_vexpand(True)
        lst = Gtk4.ListBox()
        for h in reversed(self.command_history[-50:]):
            row = Gtk4.Box(orientation=Gtk4.Orientation.VERTICAL, spacing=4)
            row.set_margin_top(8)
            row.set_margin_bottom(8)
            row.set_margin_start(12)
            row.set_margin_end(12)
            row.append(Gtk4.Label(label=f"[{h['time']}] {h['label']}", xalign=0, css_classes=["bold"]))
            row.append(Gtk4.Label(label=h.get("cmd", ""), xalign=0, css_classes=["dim-label"]))
            lst.append(row)
        scroll.set_child(lst)
        area.append(scroll)
        dialog.add_button("Close", Gtk4.ResponseType.CLOSE)
        dialog.present()

    def _show_apt_history(self):
        entries = fetch_apt_history(30)
        self._switch_page("logs")
        self.log_buffer.set_text("\n\n".join(entries) or "No APT history found.")

    def _toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()


# --- Application Entry ---
class PkgerDApp(Adw.Application if Adw else Gtk4.Application):
    def __init__(self):
        super().__init__(application_id="com.mzm.pkgerd", flags=Gio4.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = PkgerDWindow(application=self)
        win.present()


# --- CLI Mode ---
def run_cli(args):
    if args.command == "search":
        results = parallel_search(args.query, AppConfig.load())
        for r in results:
            print(f"[{r['source'].upper():7}] {r['name']:40} {r.get('desc', '')[:60]}")
        print(f"\n{len(results)} result(s)")
    elif args.command == "updates":
        if args.refresh:
            run_command(["sudo", "apt", "update"], timeout=180)
        updates = fetch_all_pending_updates()
        for u in updates:
            sev = u.get("severity", "normal")
            print(f"[{sev:10}] [{u['source']:7}] {u['name']:30} {u['from']} → {u['to']}")
        sec = sum(1 for u in updates if u.get("severity") == "security")
        print(f"\n{len(updates)} update(s), {sec} security")
    elif args.command == "health":
        pkgs = fetch_installed_packages()
        updates = fetch_all_pending_updates()
        held = fetch_held_packages()
        stats = get_system_stats()
        score = compute_health_score(len(pkgs), updates, held, stats)
        print(f"Health Score: {score}%")
        print(f"Installed: {len(pkgs)}  Updates: {len(updates)}  Held: {len(held)}")
        print(f"Cache: {human_size(stats.get('cache_size', 0))}  Kernel: {stats.get('kernel')}")
    elif args.command == "deb":
        a = analyze_deb_file(args.file)
        if a.get("error"):
            print(f"Error: {a['error']}")
            return 1
        print(f"Package:  {a['package']} {a['version']}")
        print(f"Arch:     {a['deb_arch']} (system: {a['system_arch']}) {'OK' if a['arch_ok'] else 'MISMATCH'}")
        print(f"Summary:  {a['summary']}")
        if a.get("depends"):
            print("\nDependencies:")
            for d in a["depends"]:
                mark = "OK" if d.get("status") == "ok" else "MISSING"
                print(f"  [{mark}] {d.get('dep_type')}: {d.get('clause')}")
        if a.get("extra_new"):
            print(f"\nAPT extra packages: {', '.join(a['extra_new'])}")
        if a.get("warnings"):
            print("\nWarnings:")
            for w in a["warnings"]:
                print(f"  • {w}")
        if args.install:
            if not a.get("ready") and a.get("conflicts"):
                print("\nCannot install due to conflicts.")
                return 1
            os.system(f"sudo {build_deb_install_command(args.file)}")
        return 0
    elif args.command == "install":
        src = args.source or "apt"
        cmd = {"apt": f"sudo apt install -y {shlex.quote(args.package)}",
               "flatpak": f"flatpak install -y {shlex.quote(args.package)}",
               "snap": f"snap install {shlex.quote(args.package)}"}[src]
        os.system(cmd)
    elif args.command == "gui":
        app = PkgerDApp()
        app.run(sys.argv)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} v{APP_VERSION} — Professional Package Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  pkger-d.py search firefox\n  pkger-d.py updates --refresh\n  pkger-d.py health",
    )
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search", help="Search packages across APT/Flatpak/Snap")
    p_search.add_argument("query", help="Search query")

    p_upd = sub.add_parser("updates", help="List pending updates")
    p_upd.add_argument("--refresh", action="store_true", help="Refresh APT cache first")

    sub.add_parser("health", help="Show system health score")

    p_deb = sub.add_parser("deb", help="Analyze (and optionally install) a .deb file")
    p_deb.add_argument("file", help="Path to .deb package")
    p_deb.add_argument("--install", action="store_true", help="Install after analysis")

    p_inst = sub.add_parser("install", help="Install a package")
    p_inst.add_argument("package", help="Package name")
    p_inst.add_argument("--source", choices=["apt", "flatpak", "snap"], default="apt")

    sub.add_parser("gui", help="Launch graphical interface")

    args = parser.parse_args()
    if args.command:
        sys.exit(run_cli(args))

    app = PkgerDApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
