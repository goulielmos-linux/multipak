"""Download snaps from the Snap Store without snapd.

Only the public, anonymous store API is used. Every download is checked against the
SHA3-384 digest the store publishes for that revision before it is used.
"""

import hashlib
import json
import os
import platform
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from multipak import DISPLAY_VERSION
from multipak.localization import tr

from .process import Cancelled
from .snap_meta import ARCHITECTURES, NAME
from .storage import require_space

API = "https://api.snapcraft.io/v2/snaps/info/"
MAX_SNAP = 8 * 1024**3
CHUNK = 1024 * 1024
LINK = re.compile(
    r"^(?:snap:(?://)?|https://(?:www\.)?snapcraft\.io/(?:install/)?)([a-z0-9](?:-?[a-z0-9])*)/?"
    r"(?:[?#].*)?$"
)


@dataclass(frozen=True)
class Resource:
    """A component published for one revision of a snap."""

    name: str
    kind: str
    revision: str
    version: str
    url: str
    sha3_384: str
    size: int

    def to_dict(self):
        return dict(self.__dict__)


def _resource(item):
    download = item.get("download") or {}
    kind = str(item.get("type") or "")
    result = Resource(
        str(item.get("name") or ""),
        kind.removeprefix("component/"),
        str(item.get("revision") or ""),
        str(item.get("version") or ""),
        str(download.get("url") or ""),
        str(download.get("sha3-384") or ""),
        int(download.get("size") or 0),
    )
    valid = (
        kind.startswith("component/")
        and NAME.fullmatch(result.name)
        and re.fullmatch(r"[0-9]{1,12}", result.revision)
        and result.url.startswith("https://")
        and re.fullmatch(r"[0-9a-f]{96}", result.sha3_384)
        and 0 < result.size <= MAX_SNAP
    )
    return result if valid else None


@dataclass(frozen=True)
class Release:
    name: str
    revision: str
    version: str
    url: str
    sha3_384: str
    size: int
    base: str
    confinement: str
    # Components published with this revision, available to install with it.
    resources: tuple = ()
    # The channel it was taken from, such as latest/stable.
    channel: str = "latest/stable"
    # (channel, version) of every channel published for this computer, to choose from.
    channels: tuple = ()


RISKS = ("stable", "candidate", "beta", "edge")
CHANNEL = re.compile(r"(?:([a-z0-9][a-z0-9._-]{0,63})/)?(stable|candidate|beta|edge)")


def store_channel(text):
    """The channel a snap: or snapcraft.io link asks for (?channel=latest/edge, or a
    risk alone), as track/risk; empty when it names none or one MultiPak does not
    follow, such as a branch."""
    parts = urllib.parse.urlsplit((text or "").strip())
    if not LINK.match((text or "").strip()):
        return ""
    values = urllib.parse.parse_qs(parts.query).get("channel") or []
    match = CHANNEL.fullmatch(values[0]) if len(values) == 1 else None
    if not match:
        return ""
    return f"{match[1] or 'latest'}/{match[2]}"


def store_name(text):
    """The snap name in a snap: or snapcraft.io link, or a bare name; else empty."""
    text = (text or "").strip()
    if match := LINK.match(text):
        return match[1]
    return text if NAME.fullmatch(text) and re.search("[a-z]", text) else ""


def architecture():
    machine = platform.machine()
    return ARCHITECTURES.get(machine, machine)


def _opener():
    # Redirects to the store's download network must stay on HTTPS.
    class HttpsOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.startswith("https://"):
                raise urllib.error.URLError(tr("The store redirected to an insecure address."))
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(HttpsOnly)


def _request(url):
    return urllib.request.Request(
        url,
        headers={
            "Snap-Device-Series": "16",
            "User-Agent": f"MultiPak/{DISPLAY_VERSION}",
            "Accept": "application/json",
        },
    )


def _channels(published):
    """(track/risk, version) of each published channel: latest first, then the other
    tracks, each from the most stable risk."""
    found = {}
    for item in published:
        channel = item["channel"]
        track, risk = str(channel.get("track") or ""), str(channel.get("risk") or "")
        if risk in RISKS and CHANNEL.fullmatch(f"{track}/{risk}"):
            found[(track != "latest", track, RISKS.index(risk))] = (
                f"{track}/{risk}",
                str(item.get("version", ""))[:64],
            )
    return tuple(found[key] for key in sorted(found))


def release(name, *, opener=None, arch=None, channel=""):
    """The revision of name in channel (track/risk) for this computer's architecture;
    without one, the latest stable. A closed risk follows the next more stable one in
    its track, as the store and snapd do."""
    if not NAME.fullmatch(name):
        raise ValueError(tr("Invalid snap name: {0}").format(name))
    arch = arch or architecture()
    query = urllib.parse.urlencode(
        {
            "fields": "revision,version,download,base,confinement,type,resources",
            "architecture": arch,
        }
    )
    try:
        with (opener or _opener()).open(_request(API + name + "?" + query), timeout=30) as reply:
            data = json.loads(reply.read(4 * 1024**2))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ValueError(tr("The Snap Store has no snap named {0}.").format(name)) from error
        raise RuntimeError(tr("The Snap Store could not be reached: {0}").format(error)) from error
    except (OSError, ValueError) as error:
        raise RuntimeError(tr("The Snap Store could not be reached: {0}").format(error)) from error
    published = [
        item
        for item in data.get("channel-map") or []
        if isinstance(item, dict) and (item.get("channel") or {}).get("architecture") == arch
    ]
    if channel:
        track, risk = channel.split("/")
        found = None
        for wanted in RISKS[RISKS.index(risk) :: -1]:
            found = next(
                (
                    item
                    for item in published
                    if item["channel"].get("track") == track
                    and item["channel"].get("risk") == wanted
                ),
                None,
            )
            if found:
                break
        if found is None:
            raise ValueError(
                tr(
                    "The Snap Store has no release of {0} in the channel {1} for this computer."
                ).format(name, channel)
            )
        item = found
    else:
        entries = [item for item in published if item["channel"].get("risk") == "stable"]
        entries.sort(key=lambda item: (item["channel"].get("track") != "latest",))
        if not entries:
            raise ValueError(
                tr("The Snap Store has no stable release of {0} for this computer.").format(name)
            )
        item = entries[0]
    download = item.get("download") or {}
    result = Release(
        name,
        str(item.get("revision", "")),
        str(item.get("version", "")),
        str(download.get("url", "")),
        str(download.get("sha3-384", "")),
        int(download.get("size") or 0),
        str(item.get("base") or ""),
        str(item.get("confinement") or "strict"),
        tuple(
            resource
            for resource in (
                _resource(entry) for entry in item.get("resources") or [] if isinstance(entry, dict)
            )
            if resource is not None
        ),
        channel or f"{item['channel'].get('track', 'latest')}/stable",
        _channels(published),
    )
    if (
        not re.fullmatch(r"[0-9]{1,12}", result.revision)
        or not result.url.startswith("https://")
        or not re.fullmatch(r"[0-9a-f]{96}", result.sha3_384)
        or not 0 < result.size <= MAX_SNAP
    ):
        raise RuntimeError(
            tr("The Snap Store returned incomplete information for {0}.").format(name)
        )
    return result


def _matches(path, item):
    """Whether path already holds exactly item: its size and SHA3-384 digest."""
    try:
        if not path.is_file() or path.stat().st_size != item.size:
            return False
        digest = hashlib.sha3_384()
        with path.open("rb") as stream:
            while chunk := stream.read(CHUNK):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == item.sha3_384


def download(item, target: Path, *, progress=lambda *_: None, cancelled=None, opener=None):
    """Download a release or a resource to target, verifying its size and digest."""
    target = Path(target)
    if _matches(target, item):
        # Downloaded before, for example from another channel with the same revision.
        progress(item.size, item.size)
        return target
    # Its own name: two installers may download the same snap at once.
    temporary = target.with_name(f"{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.part")
    digest, received = hashlib.sha3_384(), 0
    target.parent.mkdir(parents=True, exist_ok=True)
    require_space(target.parent, item.size)
    try:
        with (opener or _opener()).open(_request(item.url), timeout=60) as reply:
            with temporary.open("wb") as stream:
                while chunk := reply.read(CHUNK):
                    if cancelled is not None and cancelled.is_set():
                        raise Cancelled
                    received += len(chunk)
                    if received > item.size:
                        raise RuntimeError(tr("The downloaded snap is larger than expected."))
                    digest.update(chunk)
                    stream.write(chunk)
                    progress(received, item.size)
        if received != item.size or digest.hexdigest() != item.sha3_384:
            raise RuntimeError(tr("The downloaded snap {0} failed verification.").format(item.name))
        temporary.replace(target)
    except (OSError, urllib.error.URLError) as error:
        message = tr("The snap {0} could not be downloaded: {1}").format(item.name, error)
        raise RuntimeError(message) from error
    finally:
        temporary.unlink(missing_ok=True)
    return target
