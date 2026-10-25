#!/usr/bin/env python3
"""Read-only runtime evidence. Run inside the image, without secrets or volumes."""

import importlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sysconfig


def command(*args, env=None):
    result = subprocess.run(args, text=True, capture_output=True, env=env)
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


tools = [
    "mount",
    "umount",
    "nsenter",
    "login",
    "perl",
    "infocmp",
    "tic",
    "getfacl",
    "setfacl",
    "systemd-homed",
    "gcc",
    "g++",
    "cc",
    "make",
]
directories = [
    Path(p) for p in ["/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin"]
]
files = sorted(
    {
        p.resolve()
        for directory in directories
        for p in directory.glob("*")
        if p.is_file()
    }
)
status = dict(
    line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
)
extensions = {}
# CPython resolves extension symbols from libpython. musl ldd alone otherwise
# reports unresolved Py* symbols even for extensions that import successfully.
libpython = str(
    Path(sysconfig.get_config_var("LIBDIR")) / sysconfig.get_config_var("LDLIBRARY")
)
for module in ["_sqlite3", "_ssl", "_uuid", "_curses", "pydantic_core._pydantic_core"]:
    spec = importlib.util.find_spec(module)
    if spec and spec.origin:
        importlib.import_module(module)
        extensions[module] = {
            "path": spec.origin,
            "import": "PASS",
            "ldd_with_libpython_preloaded": command(
                "ldd", spec.origin, env={**os.environ, "LD_PRELOAD": libpython}
            ),
        }
report = {
    "python": platform.python_version(),
    "platform": platform.platform(),
    "libc": platform.libc_ver(),
    "soabi": sysconfig.get_config_var("SOABI"),
    "uid": os.getuid(),
    "gid": os.getgid(),
    "privileges": {
        key: status[key].strip()
        for key in ["CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Seccomp"]
    },
    "namespaces": {p.name: os.readlink(p) for p in Path("/proc/self/ns").iterdir()},
    "uid_map": Path("/proc/self/uid_map").read_text(),
    "tools": {tool: shutil.which(tool) for tool in tools},
    "setid_files": {
        str(p): oct(p.stat().st_mode & 0o7777)
        for p in files
        if p.stat().st_mode & 0o6000
    },
    "file_capabilities": {
        str(p): os.getxattr(p, "security.capability").hex()
        for p in files
        if "security.capability" in os.listxattr(p)
    },
    "fstab": Path("/etc/fstab").read_text() if Path("/etc/fstab").exists() else None,
    "homed_files": [str(p) for p in Path("/usr").rglob("systemd-homed")],
    "archive_tar_files": [str(p) for p in Path("/usr").rglob("Tar.pm")],
    "component_libraries": {
        name: sorted(
            {
                str(p.resolve())
                for root in [Path("/lib"), Path("/usr/lib")]
                for p in root.rglob(name + ".so*")
                if p.is_file()
            }
        )
        for name in [
            "libmount",
            "libblkid",
            "libuuid",
            "libacl",
            "libsystemd",
            "libudev",
            "libncursesw",
            "libtinfo",
        ]
    },
    "extensions": extensions,
    "packages": command(
        "dpkg-query", "-W", "-f=${binary:Package} ${source:Package} ${Version}\n"
    )
    if shutil.which("dpkg-query")
    else command("apk", "info", "-vv"),
}
if shutil.which("perl"):
    report["archive_tar_load"] = command("perl", "-MArchive::Tar", "-e", "1")
print(json.dumps(report, indent=2))
