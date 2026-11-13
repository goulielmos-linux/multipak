"""Find dependencies an AppImage expects from the system, without running any of its code.

Only file listings, ELF headers and Python source text are read. A small trusted probe
runs with the host's own Python to check which libraries and modules exist there.
"""

import ast
import json
import mmap
import os
import re
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from multipak.localization import tr

from .process import CommandError

MAX_ELF_FILES = 400
MAX_PYTHON_FILES = 500
MAX_ELF_BYTES = 256 * 1024 * 1024
MAX_PYTHON_BYTES = 2 * 1024 * 1024
# Everything extracted for one analysis. Files are read one at a time.
MAX_TOTAL_BYTES = 768 * 1024 * 1024
PROBE_MARKER = "MULTIPAK_PROBE="
SONAME = re.compile(r"\.so(?:\.[0-9]+)*$")
BIN_DIRECTORIES = {"bin", "sbin"}
# Standard library modules that distributions ship in separate packages. The value is
# the module whose presence proves the package is installed.
SPLIT_STANDARD_LIBRARY = {"tkinter": "_tkinter", "turtle": "_tkinter", "idlelib": "_tkinter"}
# Loader and libc components are part of glibc itself; their version is checked instead.
GLIBC_COMPONENTS = re.compile(
    r"^(?:ld-linux.*|libc|libm|libdl|libpthread|librt|libutil|libresolv|libanl)\.so"
)
# Fedora package for a Python module, where the module name differs from the package.
FEDORA_PYTHON_PACKAGES = {
    "_tkinter": "python3-tkinter",
    "yaml": "python3-pyyaml",
    "gi": "python3-gobject",
    "cairo": "python3-cairo",
    "PyQt5": "python3-qt5",
    "PyQt6": "python3-pyqt6",
    "PySide6": "python3-pyside6",
    "PIL": "python3-pillow",
    "dbus": "python3-dbus",
    "serial": "python3-pyserial",
    "usb": "python3-pyusb",
    "xdg": "python3-pyxdg",
    "bs4": "python3-beautifulsoup4",
    "wx": "python3-wxpython4",
    "cv2": "python3-opencv",
    "Xlib": "python3-xlib",
    "dateutil": "python3-dateutil",
    "websocket": "python3-websocket-client",
    "zmq": "python3-zmq",
    "attr": "python3-attrs",
    "pkg_resources": "python3-setuptools",
    "magic": "python3-magic",
}
FRIENDLY_NAMES = {
    "_tkinter": "Tkinter",
    "yaml": "PyYAML",
    "gi": "PyGObject",
    "PIL": "Pillow",
}
PROBE = r"""
import importlib.util, json, os, subprocess, sys
sys.path[:] = [p for p in sys.path if p not in ("", os.getcwd())]
request = json.loads(sys.argv[1])
modules = {}
for name in request["modules"]:
    try:
        modules[name] = importlib.util.find_spec(name) is not None
    except Exception:
        modules[name] = False
cache = set()
for command in (["ldconfig", "-p"], ["/sbin/ldconfig", "-p"], ["/usr/sbin/ldconfig", "-p"]):
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        continue
    cache.update(line.split(None, 1)[0] for line in output.splitlines()[1:] if line.strip())
    if cache:
        break
directories = ("/usr/lib64", "/lib64", "/usr/lib", "/lib", "/usr/local/lib", "/usr/local/lib64",
               "/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu")
libraries = {
    name: name in cache or any(os.path.exists(os.path.join(d, name)) for d in directories)
    for name in request["libraries"]
}
try:
    glibc = os.confstr("CS_GNU_LIBC_VERSION") or ""
except (AttributeError, ValueError, OSError):
    glibc = ""
print("MULTIPAK_PROBE=" + json.dumps({"modules": modules, "libraries": libraries, "glibc": glibc}))
"""


@dataclass(frozen=True)
class Dependency:
    kind: str  # "library", "python", "interpreter" or "glibc"
    name: str
    package: str = ""  # Fedora package or capability; empty when unknown or not needed
    optional: bool = False

    @property
    def label(self) -> str:
        if self.kind == "python":
            return tr("Python module {0}").format(FRIENDLY_NAMES.get(self.name, self.name))
        if self.kind == "interpreter":
            return tr("Python 3 interpreter")
        if self.kind == "glibc":
            return tr("GNU C Library {0} or newer").format(self.name)
        return tr("Library {0}").format(self.name)


@dataclass(frozen=True)
class DependencyReport:
    # What the host system lacks: decides whether the container is offered.
    missing: tuple[Dependency, ...] = ()
    incomplete: bool = False
    # Everything the AppImage expects from outside it, present on the host or not. The
    # container is a different system, so these are all checked there.
    requirements: tuple[Dependency, ...] = ()

    @property
    def required(self) -> tuple[Dependency, ...]:
        return tuple(item for item in self.missing if not item.optional)

    @property
    def packages(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.package for item in self.missing if item.package))

    @property
    def container_packages(self) -> tuple[str, ...]:
        """Packages to request in the container. DNF skips the ones already installed."""
        items = (*self.requirements, *self.missing)
        return tuple(dict.fromkeys(item.package for item in items if item.package))

    @staticmethod
    def _lines(items) -> str:
        lines = []
        for item in items:
            line = "• " + item.label
            if item.optional:
                line += " " + tr("(optional)")
            lines.append(line)
        return "\n".join(lines)

    def text(self) -> str:
        """Plain text for the dependency window. Names come from the AppImage: never HTML."""
        return self._lines(self.missing)

    def requirements_text(self) -> str:
        """Plain text: every component the AppImage expects from outside it."""
        return self._lines(self.requirements or self.missing)


@dataclass(frozen=True)
class ElfDependencies:
    needed: tuple[str, ...]
    glibc: tuple[int, ...] | None
    is_64bit: bool


def _version(text):
    return tuple(int(part) for part in text.split("."))


def elf_dependencies(data: bytes) -> ElfDependencies | None:
    """Read DT_NEEDED and GLIBC_* version requirements from an ELF file's dynamic section."""
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[4] not in (1, 2) or data[5] not in (1, 2):
        return None
    is64, endian = data[4] == 2, "<" if data[5] == 1 else ">"
    try:
        if is64:
            phoff = struct.unpack_from(endian + "Q", data, 32)[0]
            phentsize, phnum = struct.unpack_from(endian + "HH", data, 54)
        else:
            phoff = struct.unpack_from(endian + "I", data, 28)[0]
            phentsize, phnum = struct.unpack_from(endian + "HH", data, 42)
        loads, dynamic = [], None
        for index in range(min(phnum, 256)):
            base = phoff + index * phentsize
            if is64:
                ptype, _flags, offset, vaddr, _paddr, filesz = struct.unpack_from(
                    endian + "IIQQQQ", data, base
                )
            else:
                ptype, offset, vaddr, _paddr, filesz = struct.unpack_from(
                    endian + "IIIII", data, base
                )
            if ptype == 1:
                loads.append((vaddr, offset, filesz))
            elif ptype == 2:
                dynamic = (offset, filesz)
        if dynamic is None:
            return ElfDependencies((), None, is64)  # Statically linked.

        def file_offset(address):
            for vaddr, offset, size in loads:
                if vaddr <= address < vaddr + size:
                    return address - vaddr + offset
            raise ValueError("address outside the file")

        entries, step = {}, 16 if is64 else 8
        needed_offsets = []
        start, size = dynamic
        for position in range(start, min(start + size, len(data) - step + 1), step):
            tag, value = struct.unpack_from(endian + ("qQ" if is64 else "iI"), data, position)
            if tag == 0:
                break
            if tag == 1:
                needed_offsets.append(value)
            else:
                entries[tag] = value
        strtab = file_offset(entries[5])

        def string(offset):
            begin = strtab + offset
            end = data.find(b"\0", begin, begin + 4096)
            if end < 0:
                raise ValueError("unterminated string")
            return data[begin:end].decode("utf-8", "replace")

        needed = tuple(string(offset) for offset in needed_offsets)
        glibc = None
        if 0x6FFFFFFE in entries:
            position = file_offset(entries[0x6FFFFFFE])
            for _ in range(min(entries.get(0x6FFFFFFF, 0), 256)):
                _ver, count, _file, aux, following = struct.unpack_from(
                    endian + "HHIII", data, position
                )
                auxiliary = position + aux
                for _ in range(min(count, 256)):
                    _hash, _flags, _other, name, next_aux = struct.unpack_from(
                        endian + "IHHII", data, auxiliary
                    )
                    match = re.fullmatch(r"GLIBC_([0-9]+(?:\.[0-9]+)+)", string(name))
                    if match:
                        glibc = max(glibc or (0,), _version(match[1]))
                    if not next_aux:
                        break
                    auxiliary += next_aux
                if not following:
                    break
                position += following
        return ElfDependencies(needed, glibc, is64)
    except (struct.error, ValueError, KeyError):
        return None


class _Imports(ast.NodeVisitor):
    def __init__(self):
        self.required, self.optional = set(), set()
        self._optional_depth = 0

    def _add(self, name):
        top = name.split(".", 1)[0]
        (self.optional if self._optional_depth else self.required).add(top)

    def visit_Import(self, node):
        for alias in node.names:
            self._add(alias.name)

    def visit_ImportFrom(self, node):
        if node.level == 0 and node.module and node.module != "__future__":
            self._add(node.module)

    def _visit_optional(self, node):
        self._optional_depth += 1
        self.generic_visit(node)
        self._optional_depth -= 1

    # Imports inside functions or guarded by an exception handler are optional features.
    visit_FunctionDef = visit_AsyncFunctionDef = _visit_optional

    def visit_Try(self, node):
        if node.handlers:
            self._optional_depth += 1
            for statement in node.body:
                self.visit(statement)
            self._optional_depth -= 1
            for part in (*node.handlers, *node.orelse, *node.finalbody):
                self.visit(part)
        else:
            self.generic_visit(node)

    visit_TryStar = visit_Try


def python_imports(source: str) -> tuple[set[str], set[str]] | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    visitor = _Imports()
    visitor.visit(tree)
    return visitor.required, visitor.optional - visitor.required


def fedora_library_package(soname: str, is_64bit: bool = True) -> str:
    return f"{soname}()(64bit)" if is_64bit else soname


def fedora_python_package(module: str) -> str:
    if module in FEDORA_PYTHON_PACKAGES:
        return FEDORA_PYTHON_PACKAGES[module]
    # Fedora provides python3dist(name) for most distributions named like their module.
    return f"python3dist({module.lower().replace('_', '-')})"


def package_label(spec: str) -> str:
    """How a requested Fedora package or capability is shown to the user."""
    module = next((key for key, value in FEDORA_PYTHON_PACKAGES.items() if value == spec), None)
    if module is not None:
        return Dependency("python", module).label
    if match := re.fullmatch(r"python3dist\((.+)\)", spec):
        return Dependency("python", match[1]).label
    if match := re.fullmatch(r"(.+\.so[0-9.]*)(?:\(\)\(64bit\))?", spec):
        return Dependency("library", match[1]).label
    return spec


def _bin_or_library(name: str) -> bool:
    path = PurePosixPath(name)
    return (len(path.parts) >= 2 and path.parts[-2] in BIN_DIRECTORIES) or bool(
        SONAME.search(path.name)
    )


def _uses_host_python(apprun: bytes, names: list[str]) -> bool:
    if any(re.fullmatch(r"(?:usr/|opt/[^/]+/)?(?:bin/python3(?:\.[0-9]+)?)", n) for n in names):
        return False  # The AppImage brings its own interpreter.
    text = apprun[:65536].decode("utf-8", "replace")
    return apprun[:2] == b"#!" and re.search(r"\bpython3?\b", text) is not None


def host_probe(runner, modules, libraries) -> dict | None:
    try:
        output = runner.run(
            ["python3", "-c", PROBE, json.dumps({"modules": modules, "libraries": libraries})],
            timeout=30,
        )
    except (CommandError, OSError, TimeoutError):
        return None
    for line in output.splitlines():
        if line.startswith(PROBE_MARKER):
            try:
                value = json.loads(line[len(PROBE_MARKER) :])
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


def _regular_file(root: Path, name: str) -> Path | None:
    """The extracted member, only if it is a regular file inside the extraction."""
    path = root / name
    try:
        if path.is_symlink() or not path.is_file():
            return None
        # A linked parent directory could lead outside the temporary extraction.
        if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            return None
    except (OSError, RuntimeError):
        return None
    return path


def _inspect_file(path: Path, name: str):
    """Python imports or ELF dependencies of one file, reading it without loading it all."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if name.endswith(".py"):
            return "python", python_imports(
                stream.read(MAX_PYTHON_BYTES).decode("utf-8", "replace")
            )
        if os.fstat(stream.fileno()).st_size < 52:
            return "elf", None
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return "elf", elf_dependencies(data)


def _listing(archive) -> dict:
    """Member types and sizes when the archive can report them; empty otherwise."""
    listing = getattr(archive, "listing", None)
    if listing is None:
        return {}
    try:
        return listing()
    except (CommandError, OSError, TimeoutError, ValueError):
        return {}


def analyze(archive, runner, *, scratch=None, probe=host_probe) -> DependencyReport:
    """archive is appimage.Archive. Files are extracted only to read them, never executed."""
    names = archive.names()
    incomplete = False
    bundled_libraries = {PurePosixPath(name).name for name in names if SONAME.search(name)}
    apprun = archive.read("AppRun") if "AppRun" in names else b""
    host_python = _uses_host_python(apprun, names)
    elf_names = [name for name in names if _bin_or_library(name)]
    python_names = [name for name in names if name.endswith(".py")] if host_python else []
    if len(elf_names) > MAX_ELF_FILES or len(python_names) > MAX_PYTHON_FILES:
        incomplete = True
    candidates = sorted({*elf_names[:MAX_ELF_FILES], *python_names[:MAX_PYTHON_FILES]} - {"AppRun"})
    # Choose within the size limits before extracting anything, when sizes are known.
    entries = _listing(archive)
    selected, planned = [], 0
    for name in candidates:
        kind, size = entries.get(name, ("?", None))
        if kind not in ("-", "?"):
            continue  # Directories, links and devices are never read.
        limit = MAX_PYTHON_BYTES if name.endswith(".py") else MAX_ELF_BYTES
        if size is not None and (size > limit or planned + size > MAX_TOTAL_BYTES):
            incomplete = True
            continue
        planned += size or 0
        selected.append(name)
    elf_needed, glibc = {}, None
    imports_required, imports_optional = set(), set()

    def record_elf(info):
        nonlocal glibc
        if info is None:
            return
        for soname in info.needed:
            elf_needed.setdefault(soname, info.is_64bit)
        if info.glibc and (glibc is None or info.glibc > glibc):
            glibc = info.glibc

    if apprun[:4] == b"\x7fELF":
        record_elf(elf_dependencies(apprun))
    with tempfile.TemporaryDirectory(prefix="multipak-analysis-", dir=scratch) as directory:
        root = Path(directory) / "files"
        archive.extract_paths(root, selected)
        used = 0
        for name in selected:
            path = _regular_file(root, name)
            if path is None:
                continue
            size = path.stat().st_size
            limit = MAX_PYTHON_BYTES if name.endswith(".py") else MAX_ELF_BYTES
            if size > limit or used + size > MAX_TOTAL_BYTES:
                incomplete = True
                path.unlink(missing_ok=True)
                continue
            used += size
            try:
                kind, result = _inspect_file(path, name)
            except (OSError, ValueError):
                incomplete = True
                continue
            finally:
                # Free the space as soon as each file has been read.
                path.unlink(missing_ok=True)
            if kind == "python":
                if result is None:
                    incomplete = True
                    continue
                imports_required |= result[0]
                imports_optional |= result[1]
            else:
                record_elf(result)

    external = sorted(
        soname
        for soname in elf_needed
        if soname not in bundled_libraries and not GLIBC_COMPONENTS.match(soname)
    )
    modules_required, modules_optional = set(), set()
    if host_python:
        bundled_modules = {PurePosixPath(n).stem for n in names if n.endswith(".py")}
        bundled_modules |= {
            PurePosixPath(n).parent.name for n in names if n.endswith("/__init__.py")
        }
        standard = set(getattr(sys, "stdlib_module_names", ())) - set(SPLIT_STANDARD_LIBRARY)

        def external_modules(found):
            return {
                SPLIT_STANDARD_LIBRARY.get(module, module)
                for module in found
                if module not in standard and module not in bundled_modules
            }

        modules_required = external_modules(imports_required)
        modules_optional = external_modules(imports_optional) - modules_required
    modules = sorted(modules_required | modules_optional)
    requirements = [
        Dependency("library", soname, fedora_library_package(soname, elf_needed[soname]))
        for soname in external
    ]
    if host_python:
        requirements.insert(0, Dependency("interpreter", "python3", "python3"))
    requirements.extend(
        Dependency("python", module, fedora_python_package(module), module not in modules_required)
        for module in modules
    )
    requirements = tuple(requirements)
    result = probe(runner, modules, external)
    if result is None:
        # Without the host probe nothing can be confirmed missing, except the interpreter.
        missing = (Dependency("interpreter", "python3", "python3"),) if host_python else ()
        return DependencyReport(missing, True, requirements)
    available_modules = result.get("modules") or {}
    available_libraries = result.get("libraries") or {}
    missing = []
    host_glibc = re.search(r"([0-9]+(?:\.[0-9]+)+)", str(result.get("glibc", "")))
    if glibc and host_glibc and _version(host_glibc[1]) < glibc:
        missing.append(Dependency("glibc", ".".join(map(str, glibc))))
    missing.extend(
        Dependency("library", soname, fedora_library_package(soname, elf_needed[soname]))
        for soname in external
        if available_libraries.get(soname) is False
    )
    missing.extend(
        Dependency("python", module, fedora_python_package(module), module not in modules_required)
        for module in modules
        if available_modules.get(module) is False
    )
    return DependencyReport(tuple(missing), incomplete, requirements)


# Messages printed when a program cannot start because something is missing.
_LIBRARY_ERROR = re.compile(
    r"(?:error while loading shared libraries|ImportError): ([^\s:]+\.so[0-9.]*): "
    r"cannot open shared object file"
)
_MODULE_ERROR = re.compile(r"No module named '([A-Za-z_][A-Za-z0-9_]*)")
_GLIBC_ERROR = re.compile(r"version [`']GLIBC_([0-9]+(?:\.[0-9]+)+)' not found")


def startup_failures(output: str, is_64bit: bool = True) -> tuple[Dependency, ...]:
    """Recognize only well-known startup errors; anything else is not a dependency."""
    found = {}
    for match in _LIBRARY_ERROR.finditer(output):
        soname = match[1]
        found[("library", soname)] = Dependency(
            "library", soname, fedora_library_package(soname, is_64bit)
        )
    for match in _MODULE_ERROR.finditer(output):
        module = SPLIT_STANDARD_LIBRARY.get(match[1], match[1])
        found[("python", module)] = Dependency("python", module, fedora_python_package(module))
    for match in _GLIBC_ERROR.finditer(output):
        found[("glibc", match[1])] = Dependency("glibc", match[1])
    return tuple(found.values())
