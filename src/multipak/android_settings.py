"""Input configuration for owned Android instances; never routes to personal Waydroid."""

import json

from .android import valid_package_id
from .android_instances import InstanceWaydroid, discover, ensure_runtime, valid_id
from .localization import tr


def owned(runner, identifier, package):
    if not valid_id(identifier) or not valid_package_id(package):
        raise ValueError(tr("Select an application from a MultiPak Android container."))
    entry = next((item for item in discover(runner) if item["id"] == identifier), None)
    if entry is None:
        raise ValueError(tr("Select an application from a MultiPak Android container."))
    return entry


def configure(
    runner, identifier, package, *, touch=None, keyboard=None, navigation=None, arrows=None
):
    entry = owned(runner, identifier, package)
    ensure_runtime(runner)
    arguments = ["app-config", "get", package]
    if touch is not None and keyboard is not None:
        arguments = [
            "app-config",
            "set",
            package,
            "--touch",
            "on" if touch else "off",
            "--keyboard",
            "on" if keyboard else "off",
        ]
        if navigation is not None:
            arguments.extend(["--navigation", "on" if navigation else "off"])
        if arrows is not None:
            arguments.extend(["--arrows", "on" if arrows else "off"])
    output = InstanceWaydroid(runner, identifier).command(*arguments, timeout=90 if arrows else 20)
    try:
        result = json.loads(output)
        if not isinstance(result, dict) or any(
            type(result.get(key)) is not bool
            for key in ("touch", "keyboard", "navigation", "arrows")
        ):
            raise ValueError
    except (ValueError, TypeError) as error:
        raise RuntimeError(tr("Android did not confirm the input preferences.")) from error
    return {**result, "label": entry["label"]}


def reopen(runner, identifier, package, progress):
    result = configure(runner, identifier, package)
    android = InstanceWaydroid(runner, identifier)
    progress(-1, tr("Reopening the application…"))
    android.command("prop", "set", "waydroid.active_apps", package)
    from .privileged import call

    output = call(runner, "android", "reopen-app", identifier, package, timeout=150)
    if "MULTIPAK_ANDROID_APP_REOPENED" not in output.splitlines():
        raise RuntimeError(tr("Android did not confirm that the application was reopened."))
    return result
