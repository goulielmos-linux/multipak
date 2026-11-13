"""Find and install new versions of the AppImages MultiPak manages.

Where to look comes, in this order, from a link saved for the application, the update
information embedded in the AppImage (the AppImage specification's .upd_info section)
or the project page in its AppStream metadata, used only once a published file is found
to match the installed copy. Versions are never compared: an update is available when
the published file differs from the installed one.
"""

import fnmatch
import hashlib
import json
import os
import platform
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from multipak.localization import tr

from . import host
from .process import Cancelled

CACHE_SECONDS = 6 * 60 * 60
RELEASES = 20
MAX_JSON = 8 * 1024 * 1024
MAX_ZSYNC_HEADER = 64 * 1024
MAX_METAINFO = 1024 * 1024
MAX_UPDATE_INFORMATION = 4096
MAX_APPIMAGE = 8 * 1024 * 1024 * 1024
TIMEOUT = 20
USER_AGENT = "MultiPak"
ARCHITECTURES = {
    "x86_64": ("x86_64", "x86-64", "amd64", "x64"),
    "aarch64": ("aarch64", "arm64"),
    "i686": ("i686", "i386", "x86"),
    "armv7l": ("armv7l", "armv7", "armhf"),
}
GITHUB = ("github.com", "www.github.com")
GITLAB = ("gitlab.com", "www.gitlab.com")
GITEA = ("codeberg.org",)


class UpdateError(RuntimeError):
    """A problem the user can understand, such as a link that leads nowhere."""


class LinkMismatch(UpdateError):
    """The link the user entered does not lead to this application."""

    def __init__(self):
        super().__init__(tr("The link does not match this application. Enter it again."))


# Where updates come from


@dataclass(frozen=True)
class Source:
    kind: str  # "github", "gitlab", "gitea", "zsync" or "file"
    host: str = ""
    project: str = ""  # owner/repository, or a GitLab project path
    url: str = ""  # zsync and file sources
    tag: str = "latest"
    pattern: str = ""  # file name pattern; * stands for anything
    zsync: bool = False  # the pattern names .zsync files

    @property
    def link(self) -> str:
        """The address saved for the application."""
        if self.kind in ("zsync", "file"):
            return self.url
        return f"https://{self.host}/{self.project}"


def _https(text):
    parts = urllib.parse.urlsplit(text.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise UpdateError(tr("Enter an address that starts with https://."))
    return parts


def _pattern(name, tag):
    """A file name from one release as a pattern for the others: the version varies."""
    version = re.sub(r"^[vV](?=[0-9])", "", tag)
    if len(version) >= 2 and version in name:
        return name.replace(version, "*")
    return name


def parse_link(text) -> Source:
    """The source behind a link: a project page, a release file or a direct download."""
    parts = _https(text)
    host_name = parts.hostname.lower()
    path = [urllib.parse.unquote(part) for part in parts.path.split("/") if part]
    name = path[-1] if path else ""
    if host_name in GITHUB + GITEA and len(path) >= 2:
        kind = "github" if host_name in GITHUB else "gitea"
        project = "/".join(path[:2]).removesuffix(".git")
        pattern = ""
        if path[2:4] == ["releases", "download"] and len(path) >= 6:
            pattern = _pattern(name, path[4])
        return Source(
            kind, "github.com" if kind == "github" else host_name, project, pattern=pattern
        )
    if host_name in GITLAB and len(path) >= 2:
        project, _, rest = "/".join(path).partition("/-/")
        pattern = ""
        tail = rest.split("/")
        if tail[:1] == ["releases"] and len(tail) >= 4 and tail[2] == "downloads":
            pattern = _pattern(tail[-1], tail[1])
        return Source("gitlab", "gitlab.com", project.removesuffix(".git"), pattern=pattern)
    url = urllib.parse.urlunsplit(parts._replace(fragment=""))
    if name.lower().endswith(".zsync"):
        return Source("zsync", url=url)
    if name.lower().endswith(".appimage"):
        return Source("file", url=url)
    raise UpdateError(tr("MultiPak cannot look for updates at this address."))


def parse_update_information(text) -> Source | None:
    """The AppImage specification's update information, when MultiPak supports its kind."""
    fields = text.strip().split("|")
    if fields[0] == "zsync" and len(fields) == 2:
        try:
            return Source("zsync", url=urllib.parse.urlunsplit(_https(fields[1])))
        except UpdateError:
            return None
    if fields[0] == "gh-releases-zsync" and len(fields) == 5:
        owner, repository, tag, pattern = fields[1:]
        valid = re.compile(r"[A-Za-z0-9_.-]{1,100}")
        if valid.fullmatch(owner) and valid.fullmatch(repository) and pattern:
            return Source(
                "github",
                "github.com",
                f"{owner}/{repository}",
                tag=tag or "latest",
                pattern=pattern,
                zsync=True,
            )
    return None


def update_information(path) -> str:
    """The .upd_info ELF section of an AppImage, read without running it."""
    with Path(path).open("rb") as stream:
        header = stream.read(64)
        if len(header) < 52 or header[:4] != b"\x7fELF" or header[4] not in (1, 2):
            return ""
        is64, endian = header[4] == 2, "<" if header[5] == 1 else ">"
        if is64:
            offset = struct.unpack_from(endian + "Q", header, 40)[0]
            size, count, names = struct.unpack_from(endian + "HHH", header, 58)
            layout = endian + "IIQQQQ"
        else:
            offset = struct.unpack_from(endian + "I", header, 32)[0]
            size, count, names = struct.unpack_from(endian + "HHH", header, 46)
            layout = endian + "IIIIII"
        if not offset or not count or count > 512 or names >= count:
            return ""
        if size < struct.calcsize(layout):
            return ""
        stream.seek(offset)
        table = stream.read(size * count)
        if len(table) < size * count:
            return ""
        sections = [struct.unpack_from(layout, table, index * size) for index in range(count)]

        def read(section, limit):
            stream.seek(section[4])
            return stream.read(min(section[5], limit))

        strings = read(sections[names], 65536)
        for section in sections:
            if section[0] >= len(strings):
                continue
            end = strings.find(b"\0", section[0])
            if strings[section[0] : end] == b".upd_info":
                data = read(section, MAX_UPDATE_INFORMATION)
                return data.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    return ""


_APPSTREAM_URL = re.compile(
    r"<url\b[^>]*\btype\s*=\s*[\"'](?:vcs-browser|homepage|bugtracker|contribute|help)[\"']"
    r"[^>]*>\s*([^<\s]+)\s*</url>"
)


def appstream_sources(appdir) -> list[Source]:
    """Project pages named in the application's AppStream metadata."""
    appdir = Path(appdir)
    found = []
    for folder in ("usr/share/metainfo", "usr/share/appdata"):
        directory = appdir / folder
        try:
            if directory.is_symlink() or not directory.is_dir():
                continue
            paths = sorted(directory.iterdir())
        except OSError:
            continue
        for path in paths[:20]:
            try:
                if path.is_symlink() or not path.is_file() or path.suffix != ".xml":
                    continue
                text = path.read_bytes()[:MAX_METAINFO].decode("utf-8", "replace")
            except OSError:
                continue
            for match in _APPSTREAM_URL.finditer(text):
                try:
                    source = parse_link(match[1].replace("&amp;", "&"))
                except UpdateError:
                    continue
                if source.kind in ("github", "gitlab", "gitea") and source not in found:
                    found.append(source)
    return found


# Network


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise UpdateError(tr("The download was redirected to an insecure address."))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Http:
    """HTTPS requests. Replaced in tests."""

    def __init__(self):
        self.opener = urllib.request.build_opener(_HttpsOnly())

    def open(self, url, *, method="GET", headers=None, timeout=TIMEOUT):
        _https(url)
        request = urllib.request.Request(
            url, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        try:
            return self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise UpdateError(tr("The address was not found.")) from error
            if error.code in (403, 429) and error.headers.get("X-RateLimit-Remaining") == "0":
                raise UpdateError(
                    tr("GitHub limits how often it can be consulted. Try again later.")
                ) from error
            raise UpdateError(
                tr("The server answered with error {0}.").format(error.code)
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise UpdateError(tr("Could not connect: {0}").format(reason)) from error

    def json(self, url, headers=None):
        with self.open(url, headers={"Accept": "application/json", **(headers or {})}) as response:
            data = response.read(MAX_JSON + 1)
        if len(data) > MAX_JSON:
            raise UpdateError(tr("The server's answer is too large."))
        try:
            return json.loads(data)
        except ValueError as error:
            raise UpdateError(tr("The server's answer could not be read.")) from error

    def size(self, url):
        """The size of the file at url, when the server reports it."""
        with self.open(url, method="HEAD") as response:
            value = response.headers.get("Content-Length", "")
        return int(value) if value.isdigit() else None

    def prefix(self, url, limit):
        with self.open(url, headers={"Range": f"bytes=0-{limit - 1}"}) as response:
            return response.read(limit)

    def download(self, url, target, *, progress=None, cancelled=None, limit=MAX_APPIMAGE):
        with self.open(url, timeout=60) as response, Path(target).open("xb") as stream:
            total = response.headers.get("Content-Length", "")
            total = int(total) if total.isdigit() else 0
            if total > limit:
                raise UpdateError(tr("The file is too large."))
            received = 0
            while chunk := response.read(1024 * 1024):
                if cancelled is not None and cancelled.is_set():
                    raise Cancelled
                received += len(chunk)
                if received > limit:
                    raise UpdateError(tr("The file is too large."))
                stream.write(chunk)
                if progress is not None:
                    progress(received, total)
        return received


# Releases


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int | None = None
    sha256: str = ""


@dataclass(frozen=True)
class Listed:
    tag: str
    assets: tuple[Asset, ...]


def _api_project(project):
    return urllib.parse.quote(project, safe="")


def _github(http, source):
    base = f"https://api.github.com/repos/{source.project}/releases"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if source.tag != "latest":
        data = [http.json(f"{base}/tags/{urllib.parse.quote(source.tag, safe='')}", headers)]
    else:
        data = http.json(f"{base}?per_page={RELEASES}", headers)
    result = []
    for release in data if isinstance(data, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if release.get("prerelease") and source.tag == "latest":
            continue
        assets = []
        for item in release.get("assets") or []:
            if not isinstance(item, dict):
                continue
            digest = str(item.get("digest") or "")
            assets.append(
                Asset(
                    str(item.get("name", "")),
                    str(item.get("browser_download_url", "")),
                    item.get("size") if isinstance(item.get("size"), int) else None,
                    digest.removeprefix("sha256:") if digest.startswith("sha256:") else "",
                )
            )
        result.append(Listed(str(release.get("tag_name", "")), tuple(assets)))
    return result


def _gitlab(http, source):
    data = http.json(
        f"https://{source.host}/api/v4/projects/{_api_project(source.project)}/releases"
        f"?per_page={RELEASES}"
    )
    result = []
    for release in data if isinstance(data, list) else []:
        if not isinstance(release, dict) or release.get("upcoming_release"):
            continue
        links = (release.get("assets") or {}).get("links") or []
        assets = tuple(
            Asset(str(item.get("name", "")), str(item.get("direct_asset_url") or item.get("url")))
            for item in links
            if isinstance(item, dict)
        )
        result.append(Listed(str(release.get("tag_name", "")), assets))
    return result


def _gitea(http, source):
    data = http.json(
        f"https://{source.host}/api/v1/repos/{source.project}/releases?limit={RELEASES}"
    )
    result = []
    for release in data if isinstance(data, list) else []:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        assets = tuple(
            Asset(
                str(item.get("name", "")),
                str(item.get("browser_download_url", "")),
                item.get("size") if isinstance(item.get("size"), int) else None,
            )
            for item in release.get("assets") or []
            if isinstance(item, dict)
        )
        result.append(Listed(str(release.get("tag_name", "")), assets))
    return result


def releases(http, source) -> list[Listed]:
    """Published releases, newest first, without drafts or pre-releases."""
    return {"github": _github, "gitlab": _gitlab, "gitea": _gitea}[source.kind](http, source)


def _mentions(name, tokens):
    lowered = name.lower()
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", lowered) for t in tokens
    )


def choose(assets, source, *, machine=None, application=""):
    """The file of a release this computer would install, or None."""
    machine = machine or platform.machine()
    ours = ARCHITECTURES.get(machine, (machine.lower(),))
    others = tuple(t for key, tokens in ARCHITECTURES.items() if key != machine for t in tokens)
    suffix = ".zsync" if source.zsync else ".appimage"
    candidates = [
        asset
        for asset in assets
        if asset.name.lower().endswith(suffix)
        and (not source.pattern or fnmatch.fnmatchcase(asset.name, source.pattern))
        and (_mentions(asset.name, ours) or not _mentions(asset.name, others))
    ]
    if not candidates:
        return None
    words = {word for word in re.split(r"[^a-z0-9]+", application.lower()) if len(word) > 2}

    def rank(asset):
        named = sum(word in asset.name.lower() for word in words)
        return (not _mentions(asset.name, ours), -named, len(asset.name), asset.name)

    return min(candidates, key=rank)


# Checking


@dataclass
class Release:
    """The file an update would install."""

    url: str
    name: str = ""
    version: str = ""
    size: int | None = None
    sha256: str = ""
    sha1: str = ""


@dataclass
class Check:
    status: str  # "available", "current", "no-source" or "failed"
    source: str = ""
    release: Release | None = None
    error: str = ""
    checked: float = 0.0
    # The last result, shown because checking again did not work.
    stale: bool = False
    # A link the user entered, to save once the download proves it is this application.
    link: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        release = data.get("release")
        return cls(
            str(data.get("status", "failed")),
            str(data.get("source", "")),
            Release(**release) if isinstance(release, dict) else None,
            str(data.get("error", "")),
            float(data.get("checked", 0.0)),
        )


def zsync_header(http, url):
    """SHA-1, size and file address from a .zsync file's header."""
    data = http.prefix(url, MAX_ZSYNC_HEADER)
    text = data.split(b"\n\n", 1)[0].decode("utf-8", "replace")
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    sha1 = fields.get("SHA-1", "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha1):
        raise UpdateError(tr("The update information could not be read."))
    target = fields.get("URL") or PurePosixPath(urllib.parse.urlsplit(url).path).name.removesuffix(
        ".zsync"
    )
    target = urllib.parse.urljoin(url, target)
    _https(target)
    length = fields.get("Length", "")
    return sha1, int(length) if length.isdigit() else None, target


@dataclass
class Installed:
    """What is known about the installed copy, to compare with a published file."""

    sha256: str
    size: int
    path: Path
    name: str = ""
    _sha1: str = field(default="", repr=False)

    def sha1(self, cache=None):
        if not self._sha1 and cache is not None:
            self._sha1 = cache.sha1(self.sha256)
        if not self._sha1:
            with self.path.open("rb") as stream:
                self._sha1 = hashlib.file_digest(stream, "sha1").hexdigest()
            if cache is not None:
                cache.remember_sha1(self.sha256, self._sha1)
        return self._sha1


def _same(installed, asset):
    if asset.sha256:
        return asset.sha256.lower() == installed.sha256
    return asset.size is not None and asset.size == installed.size


def latest(http, source, installed, *, cache=None, machine=None) -> Release:
    """The newest published file for this source; raises UpdateError when there is none."""
    if source.kind == "zsync":
        sha1, size, url = zsync_header(http, source.url)
        return Release(
            url, PurePosixPath(urllib.parse.urlsplit(url).path).name, size=size, sha1=sha1
        )
    if source.kind == "file":
        return Release(
            source.url,
            PurePosixPath(urllib.parse.urlsplit(source.url).path).name,
            size=http.size(source.url),
        )
    for listed in releases(http, source):
        asset = choose(listed.assets, source, machine=machine, application=installed.name)
        if asset is None:
            continue
        version = re.sub(r"^[vV](?=[0-9])", "", listed.tag)
        if source.zsync:
            sha1, size, url = zsync_header(http, asset.url)
            return Release(
                url, PurePosixPath(urllib.parse.urlsplit(url).path).name, version, size, sha1=sha1
            )
        size = asset.size
        if size is None and not asset.sha256:
            size = http.size(asset.url)
        return Release(asset.url, asset.name, version, size, asset.sha256)
    raise UpdateError(tr("No AppImage for this computer was found at this address."))


def is_current(release, installed, cache=None):
    if release.sha256:
        return release.sha256.lower() == installed.sha256
    if release.sha1:
        return release.sha1 == installed.sha1(cache)
    return release.size is not None and release.size == installed.size


def confirm_project(http, source, installed):
    """The release file matching the installed copy, as a link to save; None when the
    project does not publish this AppImage."""
    checked = 0
    for listed in releases(http, source):
        for asset in listed.assets:
            if not asset.name.lower().endswith(".appimage"):
                continue
            if asset.sha256 or asset.size is not None:
                if _same(installed, asset):
                    return _release_link(source, listed.tag, asset)
            elif checked < 10:
                # GitLab does not list sizes: ask for a few, newest first.
                checked += 1
                try:
                    if http.size(asset.url) == installed.size:
                        return _release_link(source, listed.tag, asset)
                except UpdateError:
                    continue
    return None


def _release_link(source, tag, asset):
    quoted_tag, quoted_name = (urllib.parse.quote(value, safe="") for value in (tag, asset.name))
    if source.kind == "gitlab":
        return f"https://{source.host}/{source.project}/-/releases/{quoted_tag}/downloads/{quoted_name}"
    return f"https://{source.host}/{source.project}/releases/download/{quoted_tag}/{quoted_name}"


# Remembered results


class UpdateCache:
    """Results of earlier checks, so the list does not consult every server each time."""

    def __init__(self, store, now=time.time):
        self.path = store.root / "appimage-updates.json"
        self.now = now
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.data = data if isinstance(data, dict) else {}
        for key in ("checks", "sha1"):
            if not isinstance(self.data.get(key), dict):
                self.data[key] = {}

    def get(self, tx):
        entry = self.data["checks"].get(tx.id)
        if not isinstance(entry, dict) or entry.get("sha256") != tx.sha256:
            return None
        try:
            return Check.from_dict(entry)
        except (TypeError, ValueError):
            return None

    def fresh(self, check):
        return check is not None and 0 <= self.now() - check.checked < CACHE_SECONDS

    def put(self, tx, check):
        self.data["checks"][tx.id] = check.to_dict() | {"sha256": tx.sha256}

    def forget(self, transaction_id):
        self.data["checks"].pop(transaction_id, None)

    def sha1(self, sha256):
        return str(self.data["sha1"].get(sha256, ""))

    def remember_sha1(self, sha256, sha1):
        self.data["sha1"][sha256] = sha1

    def keep_only(self, transactions):
        keep = {tx.id for tx in transactions}
        hashes = {tx.sha256 for tx in transactions}
        self.data["checks"] = {k: v for k, v in self.data["checks"].items() if k in keep}
        self.data["sha1"] = {k: v for k, v in self.data["sha1"].items() if k in hashes}

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def managed(store):
    """AppImages MultiPak can update."""
    return sorted(
        (tx for tx in store.transactions() if tx.method == "appimage" and tx.status == "installed"),
        key=lambda tx: (tx.package_name.casefold(), tx.id),
    )


def installed_copy(store, tx):
    path = store.appimage_directory(tx.id) / "application.AppImage"
    if path.is_symlink() or not path.is_file():
        raise UpdateError(tr("The managed AppImage file is unavailable."))
    return Installed(tx.sha256, path.stat().st_size, path, tx.package_name)


def _save_source(store, transaction_id, link):
    with store.lock():
        tx = next((item for item in store.transactions() if item.id == transaction_id), None)
        if tx is not None and tx.appimage_update_source != link:
            tx.appimage_update_source = link
            store.save(tx)


def check_one(store, tx, http, cache=None, *, machine=None) -> Check:
    installed = installed_copy(store, tx)
    sources = []
    if tx.appimage_update_source:
        try:
            sources.append(parse_link(tx.appimage_update_source))
        except UpdateError:
            pass
    if not sources:
        embedded = parse_update_information(update_information(installed.path))
        if embedded is not None:
            sources.append(embedded)
    if not sources:
        for candidate in appstream_sources(store.appimage_directory(tx.id) / "AppDir"):
            try:
                link = confirm_project(http, candidate, installed)
            except UpdateError:
                continue
            if link:
                # Confirmed: the project publishes this very file. Remember it.
                _save_source(store, tx.id, link)
                sources.append(parse_link(link))
                break
    if not sources:
        return Check("no-source", checked=time.time())
    source = sources[0]
    release = latest(http, source, installed, cache=cache, machine=machine)
    status = "current" if is_current(release, installed, cache) else "available"
    return Check(status, source.link, release, checked=time.time())


def check(engine, *, force=False, http=None, now=time.time, machine=None):
    """Check every managed AppImage. Returns {transaction ID: Check}."""
    http = http or Http()
    store = engine.store
    cache = UpdateCache(store, now)
    transactions = managed(store)
    results = {}
    for tx in transactions:
        if engine.cancelled.is_set():
            raise Cancelled
        cached = cache.get(tx)
        if cached is not None and not force and cache.fresh(cached):
            results[tx.id] = cached
            continue
        engine.progress(-1, tr("Checking for updates to {0}…").format(tx.package_name))
        try:
            result = check_one(store, tx, http, cache, machine=machine)
            result.checked = now()
            cache.put(tx, result)
        except (UpdateError, OSError, ValueError, TypeError, KeyError) as error:
            engine._log(
                tr("Could not check for updates to {0}: {1}").format(tx.package_name, error)
            )
            if cached is not None and cached.status != "failed":
                cached.stale, cached.error = True, str(error)
                result = cached
            else:
                result = Check("failed", error=str(error), checked=now())
        results[tx.id] = result
    cache.keep_only(transactions)
    try:
        cache.save()
    except OSError as error:
        engine._log(tr("Could not save the update check: {0}").format(error))
    return results


def check_link(engine, transaction_id, text, *, http=None, machine=None) -> Check:
    """Check a link the user entered for an AppImage that does not indicate its own."""
    http = http or Http()
    tx = _transaction(engine.store, transaction_id)
    source = parse_link(text)
    installed = installed_copy(engine.store, tx)
    engine.progress(-1, tr("Checking for updates to {0}…").format(tx.package_name))
    release = latest(http, source, installed, machine=machine)
    if is_current(release, installed):
        # The published file is the installed one: the link is certainly right.
        _save_source(engine.store, tx.id, source.link)
        result = Check("current", source.link, release, checked=time.time())
        cache = UpdateCache(engine.store)
        cache.put(tx, result)
        cache.save()
        return result
    return Check("available", source.link, release, checked=time.time(), link=source.link)


def _transaction(store, transaction_id):
    tx = next((item for item in store.transactions() if item.id == transaction_id), None)
    if tx is None or tx.method != "appimage" or tx.status != "installed":
        raise RuntimeError(tr("This AppImage installation is unavailable."))
    return tx


# Running copies


def _processes():
    result = host.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10, check=False
    )
    return result.stdout


def running(store, tx, lister=_processes):
    """IDs of processes started from this AppImage."""
    directory = store.appimage_directory(tx.id)
    markers = (str(directory / "AppDir") + "/", str(directory / "application.AppImage"))
    found = []
    for line in lister().splitlines():
        pid, _, arguments = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != os.getpid() and any(m in arguments for m in markers):
            found.append(int(pid))
    return found


def close(store, tx, *, lister=_processes, signal=None, wait=10.0, sleep=time.sleep):
    """Ask the application to close, as closing its window would. Returns whether it did."""
    pids = running(store, tx, lister)
    if not pids:
        return True
    (signal or _terminate)(pids)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        sleep(0.25)
        if not running(store, tx, lister):
            return True
    return False


def _terminate(pids):
    host.run(
        ["kill", "-TERM", "--", *map(str, pids)],
        capture_output=True,
        timeout=10,
        check=False,
    )


# Downloading


@dataclass
class Prepared:
    """A downloaded new version, checked and waiting to be installed."""

    transaction_id: str
    path: Path
    name: str
    version: str
    link: str = ""
    # Packages to add to the container, and what they are, for the confirmation.
    packages: tuple[str, ...] = ()
    dependencies: str = ""
    needs_container: bool = False


def _desktop_name(archive_names):
    return next((n for n in archive_names if "/" not in n and n.endswith(".desktop")), "")


def same_application(old, new, runner):
    """Whether two AppImages are the same application: same desktop file or name."""
    from .appimage import Archive, inspect_appimage

    first, second = Archive(old, runner), Archive(new, runner)
    if _desktop_name(first.names()) == _desktop_name(second.names()):
        return True
    return inspect_appimage(old, runner).name == inspect_appimage(new, runner).name


def download(engine, transaction_id, release, *, link="", http=None) -> Prepared:
    """Download the new version next to the installed one and check it."""
    from .appimage import inspect_appimage

    http = http or Http()
    store = engine.store
    tx = _transaction(store, transaction_id)
    directory = store.appimage_directory(tx.id)
    target = directory / "update.AppImage"
    target.unlink(missing_ok=True)
    name = tx.package_name
    engine.progress(0, tr("Downloading {0}…").format(name))

    def progress(received, total):
        if total:
            engine.progress(min(99, received * 100 // total), tr("Downloading {0}…").format(name))

    try:
        http.download(release.url, target, progress=progress, cancelled=engine.cancelled)
        with target.open("rb") as stream:
            sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        if release.sha256 and sha256 != release.sha256.lower():
            raise UpdateError(tr("The downloaded file is damaged. Try again."))
        if release.sha1:
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha1").hexdigest() != release.sha1:
                    raise UpdateError(tr("The downloaded file is damaged. Try again."))
        target.chmod(0o700)
        engine.progress(-1, tr("Checking the new version…"))
        try:
            package = inspect_appimage(target, engine.runner)
        except ValueError as error:
            raise UpdateError(
                tr("The downloaded file is not a valid AppImage: {0}").format(error)
            ) from error
        if not same_application(directory / "application.AppImage", target, engine.runner):
            if link:
                raise LinkMismatch
            raise UpdateError(
                tr("The downloaded file is a different application. It was not installed.")
            )
        prepared = Prepared(tx.id, target, package.name, package.version, link)
        return _with_dependencies(engine, tx, prepared)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _with_dependencies(engine, tx, prepared):
    """What the new version needs that the current installation does not provide."""
    from .appimage import Archive
    from .appimage_dependencies import DependencyReport, analyze

    engine.progress(-1, tr("Checking the dependencies of the new version…"))
    try:
        report = analyze(
            Archive(prepared.path, engine.runner), engine.runner, scratch=engine.store.root
        )
    except Cancelled:
        raise
    except Exception as error:
        engine._log(tr("Could not check the AppImage dependencies: {0}").format(error))
        return prepared
    if tx.appimage_container:
        # The container is a different system: everything it needs is requested there.
        known = set(tx.appimage_packages)
        new = [item for item in (*report.requirements, *report.missing) if item.package]
        new = [item for item in dict.fromkeys(new) if item.package not in known]
        prepared.packages = tuple(dict.fromkeys(item.package for item in new))
        prepared.dependencies = DependencyReport(tuple(new)).text()
    elif report.required:
        prepared.needs_container = True
        prepared.packages = report.container_packages
        prepared.dependencies = report.text()
    return prepared


def discard(store, transaction_id):
    try:
        (store.appimage_directory(transaction_id) / "update.AppImage").unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def install(engine, prepared):
    """Replace the installed copy with the downloaded one."""
    from .appimage import update

    tx = update(
        engine,
        prepared.transaction_id,
        prepared.path,
        packages=prepared.packages,
        container=prepared.needs_container or None,
    )
    if prepared.link:
        _save_source(engine.store, tx.id, prepared.link)
    cache = UpdateCache(engine.store)
    tx = next(item for item in engine.store.transactions() if item.id == tx.id)
    cache.put(tx, Check("current", tx.appimage_update_source, checked=time.time()))
    try:
        cache.save()
    except OSError:
        pass
    return tx


__all__ = [
    "Check",
    "Http",
    "LinkMismatch",
    "Prepared",
    "Release",
    "Source",
    "UpdateError",
    "appstream_sources",
    "check",
    "check_link",
    "close",
    "discard",
    "download",
    "install",
    "parse_link",
    "parse_update_information",
    "running",
    "update_information",
]
