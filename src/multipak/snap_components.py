"""Optional snap components, installed as snapd installs them, without snapd.

Each component is a SquashFS published with one revision of its snap. It is shown at
/snap/<name>/components/mnt/<component>/<revision>, and the snap finds the ones
installed for its revision through $SNAP_COMPONENTS, /snap/<name>/components/<revision>.
The store's list of components for the installed revision is kept, so that the
application can ask for one later with "snapctl install +component".
"""

import json
import os
from pathlib import Path

from multipak.localization import tr

from . import snap_store
from .snap_meta import NAME
from .snap_yaml import YamlError, loads
from .store import remove_tree


def directory(store, tx):
    return store.snap_directory(tx.id) / "components"


def _resources_file(store, tx):
    return store.snap_directory(tx.id) / "resources.json"


def save_resources(store, tx, resources, requested, *, target=None):
    data = {"resources": [item.to_dict() for item in resources], "requested": list(requested)}
    path = target if target is not None else _resources_file(store, tx)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_resources(store, tx):
    """(resources, names requested at installation) saved for this installation."""
    try:
        data = json.loads(_resources_file(store, tx).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (), []
    resources = []
    for item in data.get("resources") or []:
        try:
            resources.append(snap_store.Resource(**item))
        except TypeError:
            continue
    requested = [name for name in data.get("requested") or [] if isinstance(name, str)]
    return tuple(resources), requested


def installable(info, resources):
    """Names of the components this computer can install: published and not for a kernel."""
    published = {item.name for item in resources}
    return [
        item.name
        for item in info.component_details
        if item.kind == "standard" and item.name in published
    ]


def install(runner, store, tx, info, resource, *, progress=None, cancelled=None, target_dir=None):
    """Download, check and show one component. Returns "name=revision"."""
    from .snap import extract

    name, revision = resource.name, resource.revision
    if name not in installable(info, (resource,)) or not NAME.fullmatch(name):
        raise ValueError(tr("The snap {0} has no component named {1}.").format(info.name, name))
    base = target_dir if target_dir is not None else directory(store, tx)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = base / f"{name}_{revision}.comp"
    target = base / "mnt" / name / revision
    try:
        snap_store.download(
            resource, archive, progress=progress or (lambda *_: None), cancelled=cancelled
        )
        if target.exists():
            remove_tree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        extract(runner, archive, target, marker="meta/component.yaml")
        try:
            # Checked by extract: a file of the component's own, not a link out of it.
            data = loads((target / "meta/component.yaml").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, YamlError) as error:
            raise ValueError(str(error)) from error
        if not isinstance(data, dict) or data.get("component") != f"{info.name}+{name}":
            raise ValueError(
                tr("The component {0} does not belong to {1}.").format(name, info.name)
            )
    except BaseException:
        remove_tree(target, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)
    link = base / tx.snap_revision / name
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(f"../mnt/{name}/{revision}", link)
    return f"{name}={revision}"


def remove(store, tx, name):
    base = directory(store, tx)
    link = base / tx.snap_revision / name
    if link.is_symlink():
        link.unlink()
    remove_tree(base / "mnt" / name, ignore_errors=True)


def installed(tx):
    """{name: revision} of the components installed with the snap."""
    return dict(item.partition("=")[::2] for item in tx.snap_components)


def hook_directory(store, tx, name):
    revision = installed(tx).get(name, "")
    return Path(directory(store, tx)) / "mnt" / name / revision
