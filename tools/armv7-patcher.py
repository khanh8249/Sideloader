#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path


# ================================================================
# CONSTANTS
# ================================================================

PATCH_APPLIED = 0
UNKNOWN_ERROR = 10
ALREADY_PATCHED = 20
INTERNAL_ERROR = 30

MAX_FILE_SIZE = 5 * 1024 * 1024


# ================================================================
# ARGUMENTS
# ================================================================

parser = argparse.ArgumentParser()

parser.add_argument(
    "log",
    help="Compiler/build log"
)

parser.add_argument(
    "--root",
    default=".",
    help="Repository root"
)

parser.add_argument(
    "--cache",
    default=None,
    help="Dub package cache"
)

parser.add_argument(
    "--state",
    default=".armv7-patches",
    help="Patch state directory"
)

args = parser.parse_args()

LOG = Path(args.log)
ROOT = Path(args.root).resolve()
CACHE = Path(args.cache).resolve() if args.cache else None
STATE = Path(args.state)

STATE.mkdir(parents=True, exist_ok=True)


# ================================================================
# HELPERS
# ================================================================

def log(msg):
    print(f"[ARMV7] {msg}", flush=True)


def read_log():
    try:
        return LOG.read_text(
            encoding="utf-8",
            errors="replace"
        )
    except Exception as e:
        log(f"Cannot read build log: {e}")
        sys.exit(INTERNAL_ERROR)


def hash_text(text):
    return hashlib.sha256(
        text.encode("utf-8", errors="replace")
    ).hexdigest()[:16]


def patch_id(name, file, line):
    raw = f"{name}|{file}|{line}"
    return hash_text(raw)


def already_done(pid):
    return (STATE / f"{pid}.json").exists()


def save_patch(pid, rule, file, line, before, after):
    data = {
        "rule": rule,
        "file": str(file),
        "line": line,
        "before": before,
        "after": after,
    }

    (STATE / f"{pid}.json").write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )


def backup(file):
    backup_file = file.with_suffix(
        file.suffix + ".armv7.bak"
    )

    if not backup_file.exists():
        shutil.copy2(file, backup_file)


def write_file(file, text):
    backup(file)

    file.write_text(
        text,
        encoding="utf-8"
    )


def safe_file(file):
    try:
        if file.stat().st_size > MAX_FILE_SIZE:
            return False
    except Exception:
        return False

    return file.is_file()


# ================================================================
# SOURCE FILE EXTRACTION
# ================================================================

def find_error_locations(log):
    """
    Handles common D compiler formats:

        source.d(123): Error: ...
        source.d:123: Error: ...
        /path/source.d(123): Error: ...

    Returns:
        [(Path, line, error_text)]
    """

    results = []

    lines = log.splitlines()

    patterns = [
        re.compile(
            r"(?P<file>[^\s:()]+\.d)"
            r"\((?P<line>\d+)\)"
            r"(?::|\s).*?(?P<error>"
            r"Error:.*|error:.*)"
        ),

        re.compile(
            r"(?P<file>[^\s:]+\.d)"
            r":(?P<line>\d+)"
            r":.*?(?P<error>"
            r"Error:.*|error:.*)"
        ),
    ]

    for text in lines:
        for pattern in patterns:
            m = pattern.search(text)

            if not m:
                continue

            f = Path(m.group("file"))
            line = int(m.group("line"))
            error = m.group("error")

            results.append(
                (f, line, error)
            )

            break

    return results


# ================================================================
# RESOLVE SOURCE
# ================================================================

def resolve_source(path):
    candidates = []

    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append(ROOT / path)

        if CACHE:
            candidates.append(CACHE / path)

        # Search package cache for basename.
        candidates.extend(
            CACHE.rglob(path.name)
            if CACHE and CACHE.exists()
            else []
        )

    for candidate in candidates:
        try:
            candidate = candidate.resolve()

            if candidate.exists() and safe_file(candidate):
                return candidate

        except Exception:
            pass

    return None


# ================================================================
# PATCH: ulong -> uint
# ================================================================

def patch_ulong_uint(file, line_no, error):
    text = file.read_text(
        encoding="utf-8",
        errors="replace"
    )

    lines = text.splitlines(True)

    if line_no <= 0 or line_no > len(lines):
        return False

    old = lines[line_no - 1]

    # Only handle explicit declaration/assignment cases.
    #
    # Example:
    #
    #     uint x = expression;
    #
    # becomes:
    #
    #     uint x = cast(uint)(expression);
    #
    # We deliberately don't globally replace every ulong.
    pattern = re.compile(
        r"^(?P<prefix>\s*)"
        r"(?P<type>u?int)\s+"
        r"(?P<name>[A-Za-z_]\w*)"
        r"\s*=\s*"
        r"(?P<expr>.+?);"
        r"(?P<comment>\s*(?://.*)?)$"
    )

    m = pattern.match(old.rstrip("\n"))

    if not m:
        return False

    typ = m.group("type")

    if typ != "uint":
        return False

    expr = m.group("expr").strip()

    if expr.startswith("cast(uint)"):
        return False

    newline = "\n" if old.endswith("\n") else ""

    new = (
        f"{m.group('prefix')}"
        f"uint {m.group('name')} = "
        f"cast(uint)({expr});"
        f"{m.group('comment')}"
        f"{newline}"
    )

    if new == old:
        return False

    pid = patch_id(
        "ulong-to-uint",
        file,
        line_no
    )

    if already_done(pid):
        return None

    lines[line_no - 1] = new

    write_file(
        file,
        "".join(lines)
    )

    save_patch(
        pid,
        "ulong-to-uint",
        file,
        line_no,
        old,
        new
    )

    log(
        f"PATCH ulong -> uint: "
        f"{file}:{line_no}"
    )

    return True


# ================================================================
# PATCH: LONG -> INT
# ================================================================

def patch_long_int(file, line_no, error):
    text = file.read_text(
        encoding="utf-8",
        errors="replace"
    )

    lines = text.splitlines(True)

    if line_no <= 0 or line_no > len(lines):
        return False

    old = lines[line_no - 1]

    pattern = re.compile(
        r"^(?P<prefix>\s*)"
        r"int\s+"
        r"(?P<name>[A-Za-z_]\w*)"
        r"\s*=\s*"
        r"(?P<expr>.+?);"
        r"(?P<comment>\s*(?://.*)?)$"
    )

    m = pattern.match(old.rstrip("\n"))

    if not m:
        return False

    expr = m.group("expr").strip()

    if expr.startswith("cast(int)"):
        return False

    newline = "\n" if old.endswith("\n") else ""

    new = (
        f"{m.group('prefix')}"
        f"int {m.group('name')} = "
        f"cast(int)({expr});"
        f"{m.group('comment')}"
        f"{newline}"
    )

    pid = patch_id(
        "long-to-int",
        file,
        line_no
    )

    if already_done(pid):
        return None

    lines[line_no - 1] = new

    write_file(
        file,
        "".join(lines)
    )

    save_patch(
        pid,
        "long-to-int",
        file,
        line_no,
        old,
        new
    )

    log(
        f"PATCH long -> int: "
        f"{file}:{line_no}"
    )

    return True


# ================================================================
# PATCH: UNSUPPORTED GCC FLAGS
# ================================================================

def patch_dub_flags(log_text):
    """
    Remove flags that are Linux/GNU-specific and commonly break
    Android/Clang builds.

    Only modifies dub.json / dub.sdl when the actual build log
    mentions one of the problematic flags.
    """

    flags = [
        "-march=native",
        "-mtune=native",
        "-static-libgcc",
        "-static-libstdc++",
    ]

    found = [
        flag for flag in flags
        if flag in log_text
    ]

    if not found:
        return False

    files = list(ROOT.rglob("dub.json"))

    if CACHE:
        files += list(CACHE.rglob("dub.json"))

    changed = False

    for file in files:
        if not safe_file(file):
            continue

        try:
            text = file.read_text(
                encoding="utf-8",
                errors="replace"
            )
        except Exception:
            continue

        old = text

        for flag in found:
            text = text.replace(
                f'"{flag}"',
                ""
            )

            text = text.replace(
                flag,
                ""
            )

        if text != old:
            pid = patch_id(
                "remove-android-incompatible-flags",
                file,
                0
            )

            if already_done(pid):
                continue

            write_file(file, text)

            save_patch(
                pid,
                "remove-android-incompatible-flags",
                file,
                0,
                old,
                text
            )

            log(
                f"Removed Android-incompatible flags "
                f"from {file}"
            )

            changed = True

    return changed


# ================================================================
# PATCH: GUI DEPENDENCIES
# ================================================================

def patch_gui_dependencies(log_text):
    """
    Only attempts this when the compiler actually fails because
    GUI dependencies are being pulled into the CLI build.

    We don't blindly remove GUI packages from every dub.json.
    """

    gui_names = [
        "gtk-d",
        "gtk",
        "gdk",
        "glib",
        "libadwaita",
        "x11",
    ]

    if not re.search(
        r"(gtk-d|libadwaita|gdk|glib|x11)",
        log_text,
        re.IGNORECASE
    ):
        return False

    if not re.search(
        r"(cli|frontend|dependency|package|dub)",
        log_text,
        re.IGNORECASE
    ):
        return False

    changed = False

    files = list(ROOT.rglob("dub.json"))

    if CACHE:
        files += list(CACHE.rglob("dub.json"))

    for file in files:
        if not safe_file(file):
            continue

        try:
            data = json.loads(
                file.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            continue

        deps = data.get("dependencies")

        if not isinstance(deps, dict):
            continue

        removed = []

        for key in list(deps):
            key_lower = key.lower()

            if any(
                gui == key_lower or
                key_lower.startswith(gui + ":")
                for gui in gui_names
            ):
                removed.append(key)
                del deps[key]

        if not removed:
            continue

        old = file.read_text(
            encoding="utf-8"
        )

        new = json.dumps(
            data,
            indent=2,
            ensure_ascii=False
        ) + "\n"

        pid = patch_id(
            "remove-gui-dependencies",
            file,
            0
        )

        if already_done(pid):
            continue

        write_file(
            file,
            new
        )

        save_patch(
            pid,
            "remove-gui-dependencies",
            file,
            0,
            old,
            new
        )

        log(
            f"Removed GUI dependencies from {file}: "
            f"{', '.join(removed)}"
        )

        changed = True

    return changed


# ================================================================
# PATCH: ANDROID TARGET / LINUX TARGET
# ================================================================

def detect_wrong_target(log_text):
    return bool(
        re.search(
            r"arm-linux-gnueabihf|linux.*gnu.*armv7|"
            r"unsupported.*android.*target|"
            r"unknown.*target.*android",
            log_text,
            re.IGNORECASE
        )
    )


def patch_target_hint():
    """
    We don't rewrite arbitrary D source here.

    The actual target is enforced by the LDC wrapper in the
    GitHub workflow.
    """

    marker = STATE / "android-target-required"

    if marker.exists():
        return False

    marker.write_text(
        "Android ARMv7 target is enforced by ldc wrapper.\n",
        encoding="utf-8"
    )

    log(
        "Detected wrong/unsupported target reference."
    )

    log(
        "LDC wrapper already forces "
        "armv7a-linux-androideabi24."
    )

    return True


# ================================================================
# PATCH: API / COMPILER VERSION MISMATCH
# ================================================================

def patch_known_argparse(log_text, locations):
    """
    Handles the old argparse issue seen in Sideloader:

        ulong -> uint

    and some std.range.repeat incompatibility cases.

    We only patch actual source lines for integer conversion.
    """

    if not re.search(
        r"(argparse|ulong.*uint|uint.*ulong)",
        log_text,
        re.IGNORECASE
    ):
        return False

    for file, line, error in locations:

        source = resolve_source(file)

        if not source:
            continue

        if "argparse" not in str(source).lower():
            continue

        result = patch_ulong_uint(
            source,
            line,
            error
        )

        if result is None:
            return None

        if result:
            return True

    return False


# ================================================================
# PATCH: GENERIC INTEGER CONVERSION
# ================================================================

def patch_integer_conversion(log_text, locations):

    if not re.search(
        r"(cannot implicitly convert|cannot convert|"
        r"implicit conversion)",
        log_text,
        re.IGNORECASE
    ):
        return False

    if not re.search(
        r"(ulong.*uint|uint.*ulong|long.*int|int.*long)",
        log_text,
        re.IGNORECASE
    ):
        return False

    for file, line, error in locations:

        source = resolve_source(file)

        if not source:
            continue

        # ulong -> uint
        if re.search(
            r"ulong.*uint|uint.*ulong",
            error,
            re.IGNORECASE
        ):
            result = patch_ulong_uint(
                source,
                line,
                error
            )

            if result is None:
                return None

            if result:
                return True

        # long -> int
        if re.search(
            r"long.*int|int.*long",
            error,
            re.IGNORECASE
        ):
            result = patch_long_int(
                source,
                line,
                error
            )

            if result is None:
                return None

            if result:
                return True

    return False


# ================================================================
# MAIN PATCH DISPATCHER
# ================================================================

def main():
    log_text = read_log()

    locations = find_error_locations(
        log_text
    )

    log(
        f"Compiler source locations found: "
        f"{len(locations)}"
    )

    # ------------------------------------------------------------
    # 1. Wrong Android/Linux target
    # ------------------------------------------------------------

    if detect_wrong_target(log_text):

        if patch_target_hint():
            return PATCH_APPLIED

    # ------------------------------------------------------------
    # 2. Compiler integer conversion
    # ------------------------------------------------------------

    result = patch_integer_conversion(
        log_text,
        locations
    )

    if result is True:
        return PATCH_APPLIED

    if result is None:
        return ALREADY_PATCHED

    # ------------------------------------------------------------
    # 3. argparse-specific compatibility
    # ------------------------------------------------------------

    result = patch_known_argparse(
        log_text,
        locations
    )

    if result is True:
        return PATCH_APPLIED

    if result is None:
        return ALREADY_PATCHED

    # ------------------------------------------------------------
    # 4. Android-incompatible linker/compiler flags
    # ------------------------------------------------------------

    if patch_dub_flags(log_text):
        return PATCH_APPLIED

    # ------------------------------------------------------------
    # 5. GUI dependency accidentally pulled into CLI
    # ------------------------------------------------------------

    if patch_gui_dependencies(log_text):
        return PATCH_APPLIED

    # ------------------------------------------------------------
    # 6. Unknown error
    # ------------------------------------------------------------

    log("")
    log("==============================================")
    log("NO AUTOMATIC PATCH RULE MATCHED")
    log("==============================================")
    log("")
    log("This error was NOT modified automatically.")
    log("")
    log("Last compiler errors:")

    error_lines = [
        x for x in log_text.splitlines()
        if re.search(
            r"(error:|Error:|fatal error)",
            x
        )
    ]

    for line in error_lines[-50:]:
        print(line)

    return UNKNOWN_ERROR


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(INTERNAL_ERROR)
    except Exception as e:
        log(f"Internal patcher error: {e}")
        sys.exit(INTERNAL_ERROR)
