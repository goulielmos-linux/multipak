"""Build MultiPak's local SliTaz container image from the official Base ISO.

The ISO's root filesystem is read without root access and imported into Podman;
nothing from it is executed. GNU wget and CA certificates are added, because
Busybox's wget does not check HTTPS certificates."""

import gzip
import hashlib
import io
import lzma
import re
import stat
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from multipak import DISPLAY_VERSION
from multipak.localization import tr

from .models import SLITAZ_I486_ENVIRONMENT, Environment
from .process import Cancelled, CommandError
from .storage import require_space

MIRROR = "https://mirror.slitaz.org"
# The repository and podman architecture of each image.
ARCHITECTURES = {"x86_64": "amd64", "i486": "386"}
BOOTSTRAP = ("cacerts", "wget")
BOOTSTRAP_DIRECTORY = "var/cache/multipak/bootstrap"
ISO_LABEL = "io.github.multipak.slitaz-iso"
MAX_ISO = 256 * 1024 * 1024
MAX_ROOTFS = 1024 * 1024 * 1024
MAX_INDEX = 16 * 1024 * 1024
MAX_PACKAGE = 64 * 1024 * 1024
MAX_MEMBERS = 200_000
CHUNK = 1024 * 1024
SECTOR = 2048
# Not needed in a container: kernel, firmware, boot files and the live system's init.
SKIPPED = ("boot", "lib/modules", "lib/firmware", "init")
CPIO_MAGICS = (b"070701", b"070702")
MD5 = re.compile(r"[0-9a-f]{32}")
PACKAGE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._~-]*\.tazpkg")


def architecture(environment: Environment) -> str:
    return "i486" if environment == SLITAZ_I486_ENVIRONMENT else "x86_64"


def os_release(arch: str) -> bytes:
    """MultiPak's description of the image: SliTaz itself ships no os-release.

    The version tells the 32-bit environment apart, as the two use different repositories.
    """
    version = "cooking" if arch == "x86_64" else f"cooking-{arch}"
    return (
        'NAME="SliTaz GNU/Linux"\n'
        "ID=slitaz\n"
        f"VERSION_ID={version}\n"
        f'PRETTY_NAME="SliTaz GNU/Linux ({version})"\n'
        'HOME_URL="https://www.slitaz.org/"\n'
    ).encode()


def repository(arch: str) -> str:
    return f"{MIRROR}/{arch}/cooking/packages/"


def image_exists(runner, image: str) -> bool:
    try:
        runner.run(["podman", "image", "exists", image], timeout=60)
    except CommandError as error:
        if error.code == 1:
            return False
        raise
    return True


def ensure_image(
    runner,
    workspace: Path,
    environment: Environment,
    *,
    progress: Callable[[int, str], None] = lambda *_: None,
    cancelled=None,
    opener=None,
) -> None:
    """Build the environment's image unless Podman already has it."""
    if image_exists(runner, environment.image):
        return
    arch = architecture(environment)
    fetch = _Fetcher(opener or _opener(), cancelled)
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="slitaz-image-", dir=workspace) as directory:
        work = Path(directory)
        base = f"{MIRROR}/{arch}/cooking/iso/slitaz-base-{arch}-cooking"
        checksum = fetch.text(base + ".md5", 4096).split()
        if not checksum or not MD5.fullmatch(checksum[0]):
            raise RuntimeError(tr("The SliTaz download server returned an invalid checksum."))
        iso = work / "base.iso"

        def downloading(received, total):
            percent = received * 100 // total if total else -1
            progress(percent, tr("Downloading the SliTaz system…"))

        fetch.file(base + ".iso", iso, MAX_ISO, checksum[0], downloading)
        progress(-1, tr("Preparing the SliTaz image…"))
        rootfs = work / "rootfs.cpio"
        with iso.open("rb") as stream:
            offset, size = iso_member(stream, "boot/rootfs.gz")
            require_space(work, size * 4)
            decompress(stream, offset, size, rootfs)
        iso.unlink()
        index = repository(arch)
        info = fetch.text(index + "packages.info", MAX_INDEX)
        sums = fetch.text(index + "packages.md5", MAX_INDEX)
        with rootfs.open("rb") as stream:
            installed = installed_packages(stream)
        extra = {
            "etc/os-release": os_release(arch),
            # GNU wget verifies certificates, so the 32-bit repository also uses HTTPS.
            "var/lib/tazpkg/mirror": (index + "\n").encode(),
        }
        for number, (name, md5) in enumerate(bootstrap_packages(info, sums, installed), 1):
            target = work / name
            fetch.file(index + name, target, MAX_PACKAGE, md5)
            extra[f"{BOOTSTRAP_DIRECTORY}/{number:02d}-{name}"] = target.read_bytes()
            target.unlink()
        archive = work / "rootfs.tar"
        with (
            rootfs.open("rb") as source,
            tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar,
        ):
            cpio_to_tar(source, tar, extra)
        rootfs.unlink()
        runner.run(
            [
                "podman",
                "import",
                "--arch",
                ARCHITECTURES[arch],
                "--change",
                'CMD ["/bin/sh"]',
                "--change",
                f"LABEL {ISO_LABEL}={checksum[0]}",
                "--message",
                "MultiPak: SliTaz Base ISO",
                str(archive),
                environment.image,
            ],
            timeout=1800,
        )


# ISO 9660


def iso_member(stream, path: str) -> tuple[int, int]:
    """Offset and size of a file in an ISO 9660 image, found by its path."""
    stream.seek(16 * SECTOR)
    volume = stream.read(SECTOR)
    if len(volume) != SECTOR or volume[:6] != b"\x01CD001":
        raise RuntimeError(tr("The SliTaz ISO image is not valid."))
    record = volume[156:190]
    parts = PurePosixPath(path).parts
    for depth, part in enumerate(parts):
        extent = int.from_bytes(record[2:6], "little")
        length = int.from_bytes(record[10:14], "little")
        if not record[25] & 2 or length > 16 * 1024 * 1024:
            raise RuntimeError(tr("The SliTaz ISO image is not valid."))
        stream.seek(extent * SECTOR)
        listing = stream.read(length)
        record = _iso_entry(listing, part, directory=depth < len(parts) - 1)
    extent = int.from_bytes(record[2:6], "little")
    return extent * SECTOR, int.from_bytes(record[10:14], "little")


def _iso_entry(listing: bytes, wanted: str, *, directory: bool) -> bytes:
    position = 0
    while position < len(listing):
        size = listing[position]
        if size == 0:
            # Records never cross a sector boundary; the rest of the sector is padding.
            position = (position // SECTOR + 1) * SECTOR
            continue
        record = listing[position : position + size]
        position += size
        if len(record) < 34:
            break
        name = record[33 : 33 + record[32]].decode("ascii", "replace")
        if name.split(";")[0].rstrip(".").lower() == wanted.lower():
            if bool(record[25] & 2) == directory:
                return record
    raise RuntimeError(tr("The SliTaz ISO image does not contain {0}.").format(wanted))


# rootfs.gz: one or more LZMA (or gzip) streams with CPIO archives


def decompress(stream, offset: int, size: int, target: Path) -> None:
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise RuntimeError(tr("The SliTaz ISO image is not valid."))
    written = 0
    with target.open("wb") as output:
        while data.strip(b"\0"):
            data = data.lstrip(b"\0")
            if data.startswith(b"\x1f\x8b"):
                # gzip reads its concatenated members by itself.
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as reader:
                    chunk = reader.read(MAX_ROOTFS + 1 - written)
                data = b""
            else:
                lz = lzma.LZMADecompressor(lzma.FORMAT_ALONE)
                chunk = lz.decompress(data, MAX_ROOTFS + 1 - written)
                if not lz.eof:
                    raise RuntimeError(tr("The SliTaz system archive is too large."))
                data = lz.unused_data
            written += len(chunk)
            if written > MAX_ROOTFS:
                raise RuntimeError(tr("The SliTaz system archive is too large."))
            output.write(chunk)


def _members(stream):
    """(name, mode, uid, gid, links, mtime, inode, data) of each CPIO member.

    The rootfs may hold several archives one after another, padded with zeros.
    """
    count = 0
    while True:
        # Members and the padding between archives are aligned to 4 bytes.
        header = stream.read(4)
        while header == b"\0\0\0\0":
            header = stream.read(4)
        if not header:
            return
        header += stream.read(106)
        if len(header) != 110 or header[:6] not in CPIO_MAGICS:
            raise RuntimeError(tr("The SliTaz system archive is not valid."))
        count += 1
        if count > MAX_MEMBERS:
            raise RuntimeError(tr("The SliTaz system archive is not valid."))
        field = [int(header[6 + 8 * i : 14 + 8 * i], 16) for i in range(13)]
        inode, mode, uid, gid, links, mtime, size = field[:7]
        name_size = field[11]
        raw = stream.read(name_size)
        stream.read(-(110 + name_size) % 4)
        data = stream.read(size)
        stream.read(-size % 4)
        if len(raw) != name_size or len(data) != size or not raw.endswith(b"\0"):
            raise RuntimeError(tr("The SliTaz system archive is not valid."))
        name = raw[:-1].decode("utf-8", "surrogateescape")
        if name == "TRAILER!!!":
            continue
        yield name, mode, uid, gid, links, mtime, (field[7], field[8], inode), data


def _clean(name: str) -> str | None:
    path = PurePosixPath(name)
    parts = [part for part in path.parts if part not in (".", "/")]
    if not parts:
        return None
    if path.is_absolute() or ".." in parts:
        raise RuntimeError(tr("The SliTaz system archive is not valid."))
    return "/".join(parts)


def installed_packages(stream) -> set[str]:
    prefix = "var/lib/tazpkg/installed/"
    found = set()
    for name, mode, *_ in _members(stream):
        name = _clean(name) or ""
        if stat.S_ISDIR(mode) and name.startswith(prefix) and "/" not in name[len(prefix) :]:
            found.add(name[len(prefix) :])
    return found


def cpio_to_tar(stream, tar: tarfile.TarFile, extra: dict[str, bytes]) -> None:
    """Copy the system into a TAR, without device nodes, adding MultiPak's files."""
    linked: dict[tuple[int, int, int], str] = {}
    waiting: dict[tuple[int, int, int], list] = {}
    written: set[str] = set()
    for raw, mode, uid, gid, links, mtime, key, data in _members(stream):
        name = _clean(raw)
        if name is None or name in extra:
            continue
        if any(name == skip or name.startswith(skip + "/") for skip in SKIPPED):
            continue
        if name.startswith("dev/"):
            # Podman provides /dev.
            continue
        written.add(name)
        member = tarfile.TarInfo(name)
        member.mode, member.uid, member.gid, member.mtime = mode & 0o7777, uid, gid, mtime
        if stat.S_ISDIR(mode):
            member.type = tarfile.DIRTYPE
            tar.addfile(member)
        elif stat.S_ISLNK(mode):
            member.type = tarfile.SYMTYPE
            member.linkname = data.decode("utf-8", "surrogateescape")
            tar.addfile(member)
        elif stat.S_ISREG(mode):
            if links > 1 and key in linked:
                member.type, member.linkname = tarfile.LNKTYPE, linked[key]
                tar.addfile(member)
            elif links > 1 and not data:
                # New ASCII CPIO stores hard-linked data with the last name only.
                waiting.setdefault(key, []).append(member)
            else:
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
                if links > 1:
                    linked[key] = name
                    for other in waiting.pop(key, ()):
                        other.type, other.linkname = tarfile.LNKTYPE, name
                        tar.addfile(other)
    for members in waiting.values():
        for member in members:
            tar.addfile(member, io.BytesIO(b""))
    for name, content in sorted(extra.items()):
        for parent in reversed(PurePosixPath(name).parents[:-1]):
            if str(parent) not in written:
                written.add(str(parent))
                folder = tarfile.TarInfo(str(parent))
                folder.type, folder.mode, folder.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
                tar.addfile(folder)
        member = tarfile.TarInfo(name)
        member.size, member.mode, member.mtime = len(content), 0o644, int(time.time())
        tar.addfile(member, io.BytesIO(content))


# Repository


def bootstrap_packages(info: str, sums: str, installed: set[str]) -> list[tuple[str, str]]:
    """File name and MD5 of each package GNU wget needs, dependencies first.

    packages.info gives versions and dependencies (its own checksum field is not the
    file's); packages.md5 gives the checksum of each package file.
    """
    database = {}
    for line in info.splitlines():
        fields = line.split("\t")
        if len(fields) >= 8:
            database[fields[0]] = (fields[1], fields[7].split())
    files = {}
    for line in sums.splitlines():
        fields = line.split()
        if len(fields) == 2 and MD5.fullmatch(fields[0]):
            files[fields[1]] = fields[0]
    seen = set(installed)
    order: list[tuple[str, str]] = []

    def visit(name, depth):
        if name in seen:
            return
        if name not in database or depth > 32:
            raise RuntimeError(tr("The SliTaz package {0} is not in the repository.").format(name))
        seen.add(name)
        version, dependencies = database[name]
        for dependency in dependencies:
            visit(dependency, depth + 1)
        # Package files are name-version.tazpkg, with -x86_64 or -any in the 64-bit repository.
        candidates = (f"{name}-{version}", f"{name}-{version}-x86_64", f"{name}-{version}-any")
        file = next((f"{stem}.tazpkg" for stem in candidates if f"{stem}.tazpkg" in files), None)
        if file is None or not PACKAGE_FILE.fullmatch(file):
            raise RuntimeError(tr("The SliTaz package {0} is not in the repository.").format(name))
        order.append((file, files[file]))

    for name in BOOTSTRAP:
        visit(name, 0)
    return order


def _opener():
    class HttpsOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.startswith("https://"):
                raise urllib.error.URLError(tr("The server redirected to an insecure address."))
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(HttpsOnly)


class _Fetcher:
    def __init__(self, opener, cancelled):
        self.opener, self.cancelled = opener, cancelled

    def _open(self, url):
        request = urllib.request.Request(url, headers={"User-Agent": f"MultiPak/{DISPLAY_VERSION}"})
        return self.opener.open(request, timeout=60)

    def _chunks(self, url, limit):
        try:
            with self._open(url) as reply:
                total = reply.headers.get("Content-Length", "")
                total = int(total) if total.isdigit() else 0
                if total > limit:
                    raise RuntimeError(tr("The SliTaz download is larger than expected."))
                received = 0
                while chunk := reply.read(CHUNK):
                    if self.cancelled is not None and self.cancelled.is_set():
                        raise Cancelled
                    received += len(chunk)
                    if received > limit:
                        raise RuntimeError(tr("The SliTaz download is larger than expected."))
                    yield chunk, received, total
        except (OSError, urllib.error.URLError) as error:
            raise RuntimeError(
                tr("The SliTaz system could not be downloaded: {0}").format(error)
            ) from error

    def text(self, url, limit) -> str:
        data = b"".join(chunk for chunk, *_ in self._chunks(url, limit))
        return data.decode("utf-8", "replace")

    def file(self, url, target: Path, limit, md5, progress=None) -> None:
        digest = hashlib.md5(usedforsecurity=False)
        with target.open("xb") as stream:
            for chunk, received, total in self._chunks(url, limit):
                digest.update(chunk)
                stream.write(chunk)
                if progress is not None:
                    progress(received, total)
        if digest.hexdigest() != md5:
            target.unlink()
            raise RuntimeError(
                tr("The SliTaz download {0} failed verification. Try again later.").format(
                    url.rsplit("/", 1)[-1]
                )
            )
