#!/usr/bin/env python3
"""disk_tool.py — menu-driven disk & partition utility.

Wraps common partition, swap, and filesystem administration tasks:

    1. Check & install requirements
    2. List partitions
    3. Delete partition
    4. Disable swap (swapoff)
    5. Create swap file
    6. Resize partition
    7. Create partition

Standard-library Python 3 only. Requires `parted` (and uses `lsblk`,
`fallocate`, `mkswap`, `swapon`, `swapoff` from util-linux).

WARNING: several actions are destructive or system-sensitive. Always review
the target device and confirm before committing changes.
"""

import json
import os
import re
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

REQUIRED_TOOLS = ["parted"]
# Used by individual features; part of util-linux on most systems.
OPTIONAL_TOOLS = ["lsblk", "fallocate", "mkswap", "swapon", "swapoff"]

MIB = 1024 ** 2


def is_root() -> bool:
    """Return True when running with effective UID 0."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def run(cmd, check=True, input_text=None):
    """Run a command and return the CompletedProcess.

    Raises RuntimeError on failure when `check` is True.
    """
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, input=input_text)
    except FileNotFoundError as exc:
        raise RuntimeError(f"command not found: {cmd[0]}") from exc
    if check and proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"command failed ({cmd[0]}): {message}")
    return proc


def elevated(cmd):
    """Prefix a command with `sudo` when not already running as root."""
    if is_root():
        return cmd
    if shutil.which("sudo"):
        return ["sudo"] + cmd
    print("  [!] Not running as root and `sudo` is unavailable — command may fail.")
    return cmd


def run_parted(disk, args):
    """Run a parted command, auto-answering its confirmation prompts.

    parted aborts in script mode when it would otherwise prompt, e.g.:

      * "Fix/Ignore/Cancel?" when the GPT does not cover the whole disk — the
        `-f` flag makes parted fix this instead of aborting.
      * "Partition ... is being used. Are you sure you want to continue?" —
        `-f` does NOT cover this, so we retry with `---pretend-input-tty` and
        feed a "Yes" answer.
    """
    proc = run(elevated(["parted", "-s", "-f", disk] + args), check=False)
    if proc.returncode == 0:
        return proc

    stderr = proc.stderr or ""
    if "being used" in stderr or "Are you sure" in stderr:
        retry = elevated(["parted", "---pretend-input-tty", disk] + args)
        proc = run(retry, check=False, input_text="Yes\n")
        if proc.returncode == 0:
            return proc

    message = (proc.stderr or proc.stdout or "").strip()
    raise RuntimeError(f"command failed (parted): {message}")


def confirm(prompt="Continue?", default=False):
    """Ask for explicit y/n confirmation."""
    suffix = "[y/N]" if not default else "[Y/n]"
    answer = input(f"  {prompt} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def human_size(num_bytes):
    """Format a byte count in human-readable units (base 1024)."""
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"


def parse_size(text):
    """Parse a human size such as '4G', '10GiB', '500M' into bytes."""
    normalized = text.strip().upper().replace(" ", "")
    normalized = normalized.replace("IB", "B")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGT]?B?)?", normalized)
    if not match:
        raise ValueError(f"invalid size: {text!r}")
    value = float(match.group(1))
    suffix = match.group(2) or ""
    multipliers = {
        "": 1, "B": 1,
        "K": 1024, "KB": 1024,
        "M": 1024 ** 2, "MB": 1024 ** 2,
        "G": 1024 ** 3, "GB": 1024 ** 3,
        "T": 1024 ** 4, "TB": 1024 ** 4,
    }
    if suffix not in multipliers:
        raise ValueError(f"invalid size suffix: {text!r}")
    return int(value * multipliers[suffix])


def list_disks():
    """Return the list of block devices that are physical disks."""
    proc = run(["lsblk", "--json", "--bytes", "-o", "NAME,TYPE,SIZE"])
    data = json.loads(proc.stdout or "{}")
    disks = []
    for device in data.get("blockdevices", []):
        if device.get("type") == "disk":
            disks.append(device)
    return disks


def parted_partitions(disk):
    """Parse `parted --machine` output into a list of partition dicts."""
    proc = run(["parted", "-s", "--machine", disk, "unit", "B", "print"])
    partitions = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(("BYT;", "Error:", "Warning:")):
            continue
        fields = line.split(":")
        if len(fields) >= 4 and fields[0].isdigit():
            partitions.append({
                "num": int(fields[0]),
                "start": int(fields[1].rstrip("B")),
                "end": int(fields[2].rstrip("B")),
                "size": int(fields[3].rstrip("B")),
                "fstype": fields[4],
            })
    return partitions


def next_free_start(partitions):
    """Suggest a 1 MiB-aligned start position for a new partition."""
    if not partitions:
        return MIB
    return max(p["end"] for p in partitions) + MIB


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# Feature implementations
# ---------------------------------------------------------------------------

def check_and_install_requirements():
    section("Check & install requirements")
    print("  Python runtime:")
    print(f"    - Python {sys.version.split()[0]} ({sys.executable})")

    missing_required = [tool for tool in REQUIRED_TOOLS if not shutil.which(tool)]
    missing_optional = [tool for tool in OPTIONAL_TOOLS if not shutil.which(tool)]

    if missing_required:
        print(f"  Missing required tools: {', '.join(missing_required)}")
        if confirm("Install missing required tools?"):
            install_packages(missing_required)
        else:
            print("  [i] Skipping installation; some features may not work.")
    else:
        print("  Required tools: OK (" + ", ".join(REQUIRED_TOOLS) + ")")

    if missing_optional:
        print(f"  [i] Optional tools missing: {', '.join(missing_optional)}")
    else:
        print("  Optional tools: OK (" + ", ".join(OPTIONAL_TOOLS) + ")")

    # Re-verify required tools after installation attempt.
    still_missing = [tool for tool in REQUIRED_TOOLS if not shutil.which(tool)]
    if still_missing:
        print(f"  [!] Still missing: {', '.join(still_missing)}")
    else:
        print("  Requirements check complete.")


def install_packages(packages):
    """Install packages using the first available package manager."""
    candidates = [
        ("apt-get", ["apt-get", "install", "-y"]),
        ("apt", ["apt", "install", "-y"]),
        ("dnf", ["dnf", "install", "-y"]),
        ("yum", ["yum", "install", "-y"]),
        ("zypper", ["zypper", "--non-interactive", "install"]),
        ("pacman", ["pacman", "-S", "--noconfirm"]),
        ("apk", ["apk", "add"]),
    ]
    for binary, base_cmd in candidates:
        if shutil.which(binary):
            print(f"  Using package manager: {binary}")
            if binary in ("apt-get", "apt"):
                try:
                    run(elevated([binary, "update"]))
                except RuntimeError as exc:
                    print(f"  [i] update skipped: {exc}")
            cmd = elevated(base_cmd + packages)
            try:
                run(cmd)
                print(f"  Installed: {', '.join(packages)}")
            except RuntimeError as exc:
                print(f"  [!] {exc}")
            return
    print("  [!] No supported package manager found. Install manually:")
    print(f"      {' '.join(packages)}")


def list_partitions():
    section("List partitions")
    try:
        proc = run(["lsblk", "--json", "--bytes",
                    "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,PATH"])
        data = json.loads(proc.stdout or "{}")
    except RuntimeError as exc:
        print(f"  [!] {exc}")
        return

    devices = data.get("blockdevices", [])
    if not devices:
        print("  No block devices found.")
        return

    for disk in devices:
        if disk.get("type") != "disk":
            continue
        print(f"\n  {disk['name']} — physical disk size: {human_size(disk['size'])}")
        children = [c for c in disk.get("children", []) if c.get("type") == "part"]
        if not children:
            print("    (no partitions)")
            continue
        for part in children:
            fstype = part.get("fstype") or "-"
            mount = part.get("mountpoint") or "-"
            print(f"    └─ {part['name']:<12} logical size: {human_size(part['size']):<10}"
                  f" fstype: {fstype:<8} mount: {mount}")


def delete_partition():
    section("Delete partition")
    disks = list_disks()
    if not disks:
        print("  No disks found.")
        return
    print("  Available disks:")
    for disk in disks:
        print(f"    - {disk['name']} ({human_size(disk['size'])})")

    disk = input("  Disk (e.g. /dev/sda): ").strip()
    if not disk:
        print("  [!] No disk given.")
        return
    if not disk.startswith("/dev/"):
        print("  [!] Expected a full device path like /dev/sda.")
        return

    try:
        partitions = parted_partitions(disk)
    except RuntimeError as exc:
        print(f"  [!] {exc}")
        return
    if not partitions:
        print("  No partitions found on this disk.")
        return

    print("  Partitions:")
    for part in partitions:
        print(f"    {part['num']}: {human_size(part['size'])}  fstype: {part['fstype'] or '-'}")

    try:
        number = int(input("  Partition number to delete: ").strip())
    except ValueError:
        print("  [!] Invalid partition number.")
        return

    target = next((p for p in partitions if p["num"] == number), None)
    if target is None:
        print(f"  [!] Partition {number} not found.")
        return

    print(f"  WARNING: this will permanently delete partition {disk}{number} "
          f"({human_size(target['size'])}) and all data on it.")
    if not confirm("Delete this partition?", default=False):
        print("  Aborted.")
        return

    try:
        run_parted(disk, ["rm", str(number)])
    except RuntimeError as exc:
        print(f"  [!] {exc}")
        return
    print(f"  Partition {number} deleted.")


def disable_swap():
    section("Disable swap (swapoff)")
    print("  1. Disable a specific swap partition")
    print("  2. Disable all swap partitions")
    choice = input("  Choice [1-2]: ").strip()

    if choice == "1":
        device = input("  Swap device (e.g. /dev/sda2): ").strip()
        if not device:
            print("  [!] No device given.")
            return
        if not confirm(f"Disable swap on {device}?"):
            print("  Aborted.")
            return
        try:
            run(elevated(["swapoff", device]))
        except RuntimeError as exc:
            print(f"  [!] {exc}")
            return
        print(f"  Swap disabled on {device}.")
    elif choice == "2":
        if not confirm("Disable ALL swap partitions?"):
            print("  Aborted.")
            return
        try:
            run(elevated(["swapoff", "-a"]))
        except RuntimeError as exc:
            print(f"  [!] {exc}")
            return
        print("  All swap disabled.")
    else:
        print("  [!] Invalid choice.")


def create_swap_file():
    section("Create swap file")
    path = input("  Swap file path [/swapfile]: ").strip() or "/swapfile"
    size_text = input("  Swap file size (e.g. 4G) [4G]: ").strip() or "4G"
    try:
        parse_size(size_text)
    except ValueError as exc:
        print(f"  [!] {exc}")
        return

    print(f"  Will create {path} of {size_text} and enable it.")
    if not confirm("Create and enable this swap file?"):
        print("  Aborted.")
        return

    commands = [
        (["fallocate", "-l", size_text, path], "allocating file"),
        (["chmod", "600", path], "setting permissions"),
        (["mkswap", path], "setting up swap"),
        (["swapon", path], "enabling swap"),
    ]
    for cmd, label in commands:
        try:
            run(elevated(cmd))
        except RuntimeError as exc:
            print(f"  [!] Failed while {label}: {exc}")
            print("  The swap file may be partially set up; review it manually.")
            return
        print(f"  OK: {label}")

    print("  Swap file created and enabled.")

    if confirm("Persist across reboots (append to /etc/fstab)?"):
        entry = f"{path} none swap sw 0 0\n"
        try:
            check = run(["grep", "-qsx", f"{path} none swap sw 0 0", "/etc/fstab"], check=False)
            if check.returncode == 0:
                print("  [i] Entry already present in /etc/fstab.")
            else:
                run(elevated(["tee", "-a", "/etc/fstab"]), input_text=entry)
                print("  Added entry to /etc/fstab.")
        except RuntimeError as exc:
            print(f"  [!] {exc}")


def resize_partition():
    section("Resize partition")
    disks = list_disks()
    if not disks:
        print("  No disks found.")
        return
    print("  Available disks:")
    for disk in disks:
        print(f"    - {disk['name']} ({human_size(disk['size'])})")

    disk = input("  Disk (e.g. /dev/sda): ").strip()
    if not disk.startswith("/dev/"):
        print("  [!] Expected a full device path like /dev/sda.")
        return

    try:
        partitions = parted_partitions(disk)
    except RuntimeError as exc:
        print(f"  [!] {exc}")
        return
    if not partitions:
        print("  No partitions found on this disk.")
        return

    print("  Partitions:")
    for part in partitions:
        print(f"    {part['num']}: {human_size(part['size'])}  fstype: {part['fstype'] or '-'}")

    try:
        number = int(input("  Partition number to resize: ").strip())
    except ValueError:
        print("  [!] Invalid partition number.")
        return
    target = next((p for p in partitions if p["num"] == number), None)
    if target is None:
        print(f"  [!] Partition {number} not found.")
        return

    print("  1. Resize to 100% of available disk")
    print("  2. Grow by a specific amount (e.g. +1G, +2G)")
    choice = input("  Choice [1-2]: ").strip()

    try:
        if choice == "1":
            cmd_args = ["resizepart", str(number), "100%"]
        elif choice == "2":
            amount = input("  Amount to grow by (e.g. +1G): ").strip().lstrip("+")
            new_end = target["end"] + parse_size(amount)
            cmd_args = ["resizepart", str(number), f"{new_end}B"]
        else:
            print("  [!] Invalid choice.")
            return
    except ValueError as exc:
        print(f"  [!] {exc}")
        return

    print("  WARNING: resizing a partition can cause data loss.")
    if not confirm(f"Resize partition {number} on {disk}?"):
        print("  Aborted.")
        return

    try:
        run_parted(disk, cmd_args)
    except RuntimeError as exc:
        print(f"  [!] {exc}")
        return
    print(f"  Partition {number} resized.")


def create_partition():
    section("Create partition")
    disks = list_disks()
    if not disks:
        print("  No disks found.")
        return
    print("  Available disks:")
    for disk in disks:
        print(f"    - {disk['name']} ({human_size(disk['size'])})")

    disk = input("  Disk (e.g. /dev/sda): ").strip()
    if not disk.startswith("/dev/"):
        print("  [!] Expected a full device path like /dev/sda.")
        return

    try:
        partitions = parted_partitions(disk)
    except RuntimeError as exc:
        print(f"  [!] {exc}")
        return

    start = next_free_start(partitions)
    print(f"  Partition will start at {human_size(start)}.")

    name = input("  Partition name [primary]: ").strip() or "primary"
    fstype = input("  Filesystem type [ext4]: ").strip() or "ext4"

    size_text = input(
        "  Size (e.g. 10G), or leave blank to use remaining free space: "
    ).strip()
    try:
        if size_text:
            end_bytes = start + parse_size(size_text)
        else:
            end_bytes = None  # use 100%
    except ValueError as exc:
        print(f"  [!] {exc}")
        return

    end = "100%" if end_bytes is None else f"{end_bytes}B"
    cmd_args = ["mkpart", name, fstype, f"{start}B", end]
    print(f"  Will run: parted mkpart {name} {fstype} {human_size(start)} .. "
          f"{'100%' if end == '100%' else human_size(end_bytes)}")
    if not confirm("Create this partition?"):
        print("  Aborted.")
        return

    try:
        run_parted(disk, cmd_args)
    except RuntimeError as exc:
        print(f"  [!] {exc}")
        return
    print(f"  Partition created on {disk}.")

    if confirm("Format the new partition with mkfs?"):
        device = input("  Device to format (e.g. /dev/sda1): ").strip()
        if not device:
            print("  [!] No device given.")
            return
        if not confirm(f"Format {device} as {fstype} (DESTRUCTIVE)?"):
            print("  Aborted.")
            return
        try:
            run(elevated([f"mkfs.{fstype}", device]))
        except RuntimeError as exc:
            print(f"  [!] {exc}")
            return
        print(f"  {device} formatted as {fstype}.")


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

MENU_OPTIONS = [
    ("1", "Check & install requirements", check_and_install_requirements),
    ("2", "List partitions", list_partitions),
    ("3", "Delete partition", delete_partition),
    ("4", "Disable swap (swapoff)", disable_swap),
    ("5", "Create swap file", create_swap_file),
    ("6", "Resize partition", resize_partition),
    ("7", "Create partition", create_partition),
]


def show_menu():
    print("\n" + "=" * 42)
    print(" Partition Management Tools".ljust(42))
    print("=" * 42)
    for key, label, _ in MENU_OPTIONS:
        print(f"  {key}. {label}")
    print("  q. Quit")
    print("-" * 42)


def main():
    print("WARNING: This tool can delete/resize partitions and modify swap state.")
    print("         Review every target and confirm before committing changes.")
    while True:
        show_menu()
        try:
            choice = input("Choose an option: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        if choice in ("q", "quit", "exit"):
            print("Goodbye.")
            break
        match = next((fn for key, _, fn in MENU_OPTIONS if key == choice), None)
        if match is None:
            print(f"  [!] Unknown option: {choice}")
            continue
        try:
            match()
        except KeyboardInterrupt:
            print("\n  [i] Operation interrupted; returning to menu.")
        except RuntimeError as exc:
            print(f"  [!] {exc}")


if __name__ == "__main__":
    main()
