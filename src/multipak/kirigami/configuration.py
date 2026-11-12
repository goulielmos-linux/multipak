"""Draft container settings and the confirmed selection sent to the engine."""

from dataclasses import dataclass

from PySide6.QtCore import Property, QObject, Signal, Slot

from multipak.catalog import release_tag
from multipak.environment_choices import (
    container_base,
    default_environment,
)
from multipak.localization import mark, tr
from multipak.models import (
    Environment,
    ExistingEnvironment,
    reserved_environment_name,
    valid_environment_name,
)


def _profile_identity(profile):
    family, _, tag = profile.id.partition("-")
    version = profile.resolved_version or tag
    if release_tag(family, version):
        repository = profile.image.rpartition(":")[0]
        return ("release", profile.kind, family, repository, version)
    return ("image", profile.kind, profile.id, profile.image)


@dataclass(frozen=True)
class InstallationSelection:
    environment: Environment
    existing: ExistingEnvironment | None = None
    custom_name: str = ""
    manual: bool = False

    def container_name(self, environments):
        if self.existing:
            return self.existing.name
        if self.custom_name:
            return self.custom_name
        version = self.environment.resolved_version or self.environment.id.partition("-")[2]
        if version in ("latest", "stable"):
            return tr("The name will be assigned during setup")
        return container_base(self.environment)


class Configuration(QObject):
    changed = Signal()
    MODES = (
        ("default", mark("Use the default MultiPak container")),
        ("existing", mark("Use an existing container")),
        ("custom", mark("Create a custom container")),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profiles = ()
        self.existing = ()
        self.suggested = None
        self._manual_index = 0
        self._suggested = True
        self._mode = "default"
        self._existing_name = ""
        self._name = ""

    def reset(self, profiles, existing, suggested):
        candidates = tuple(item for item in profiles if item.kind == suggested.kind)
        if suggested not in candidates:
            candidates = (suggested, *candidates)
        choices = {}
        for profile in candidates:
            key = _profile_identity(profile)
            current = choices.get(key)
            # Manual choices prefer a fixed version over an equivalent moving alias.
            family, _, tag = profile.id.partition("-")
            if current is None or release_tag(family, tag):
                choices[key] = profile
        self.profiles = tuple(choices.values())
        self.existing, self.suggested = tuple(existing), suggested
        self._manual_index = self._suggested_index()
        self._suggested, self._mode = True, "default"
        self._existing_name = self._name = ""
        self.changed.emit()

    def _suggested_index(self):
        key = _profile_identity(self.suggested)
        return next(i for i, item in enumerate(self.profiles) if _profile_identity(item) == key)

    @property
    def environment(self):
        if not self.profiles:
            return None
        if self._suggested:
            return self.suggested
        return self.profiles[self._manual_index]

    def _candidates(self, mode):
        if self.environment is None:
            return ()
        return tuple(
            sorted(
                (
                    item
                    for item in self.existing
                    if mode == "existing" and item.matches(self.environment)
                ),
                key=lambda item: item.name.casefold(),
            )
        )

    def _normalize(self):
        if self._mode == "existing":
            candidates = self._candidates(self._mode)
            if not candidates:
                self._mode, self._existing_name = "default", ""
            elif self._existing_name not in {item.name for item in candidates}:
                self._existing_name = candidates[0].name

    @Property("QVariantList", notify=changed)
    def profileOptions(self):
        return [{"title": item.title} for item in self.profiles]

    @Property(int, notify=changed)
    def profileIndex(self):
        if not self.profiles:
            return -1
        return self._suggested_index() if self._suggested else self._manual_index

    @Property(str, notify=changed)
    def suggestedTitle(self):
        return self.suggested.title if self.suggested else ""

    @Property(bool, notify=changed)
    def useSuggested(self):
        return self._suggested

    @Slot(bool)
    def chooseSuggested(self, value):
        self._suggested = value
        self._normalize()
        self.changed.emit()

    @Slot(int)
    def chooseProfile(self, index):
        if 0 <= index < len(self.profiles) and not self._suggested:
            self._manual_index = index
            self._normalize()
            self.changed.emit()

    @Property("QVariantList", notify=changed)
    def containerOptions(self):
        rows = []
        for value, label in self.MODES:
            available = value != "existing" or bool(self._candidates(value))
            rows.append(
                {
                    "value": value,
                    "label": tr(label) + ("" if available else tr(" (Unavailable)")),
                    "available": available,
                }
            )
        return rows

    @Property(str, notify=changed)
    def containerMode(self):
        return self._mode

    @Property(int, notify=changed)
    def containerModeIndex(self):
        return next(i for i, (value, _) in enumerate(self.MODES) if value == self._mode)

    @Slot(str)
    def chooseContainerMode(self, value):
        if any(row["value"] == value and row["available"] for row in self.containerOptions):
            self._mode = value
            self._normalize()
        self.changed.emit()

    @Property("QVariantList", notify=changed)
    def existingOptions(self):
        return [{"name": item.name} for item in self._candidates(self._mode)]

    @Property(int, notify=changed)
    def existingIndex(self):
        return next(
            (
                i
                for i, item in enumerate(self._candidates(self._mode))
                if item.name == self._existing_name
            ),
            -1,
        )

    @Slot(int)
    def chooseExisting(self, index):
        candidates = self._candidates(self._mode)
        if 0 <= index < len(candidates):
            self._existing_name = candidates[index].name
            self.changed.emit()

    @Property(str, notify=changed)
    def customName(self):
        return self._name

    @Slot(str)
    def setCustomName(self, value):
        self._name = value.replace(" ", "-")
        self.changed.emit()

    @Property(str, notify=changed)
    def errorMessage(self):
        if self.environment is None:
            return tr("No container base is available.")
        if self._mode == "custom":
            name = self._name.strip()
            if not name:
                return tr("Enter a name for the new container.")
            if len(name) > 63:
                return tr("The name exceeds the 63-character limit.")
            if not all(
                character.isascii() and (character.isalnum() or character in "._-")
                for character in name
            ):
                return tr(
                    "The name contains unsupported characters. Use unaccented letters, "
                    "numbers, dots (.), hyphens (-) or underscores (_)."
                )
            if not valid_environment_name(name):
                return tr("The name cannot start with a dot (.), hyphen (-) or underscore (_).")
            if any(item.name == name for item in self.existing):
                return tr("A container with that name already exists. Choose another name.")
            if reserved_environment_name(name):
                return tr(
                    "Choose a custom name. This name and the MultiPak prefixes are reserved "
                    "for containers managed by the application."
                )
        if self._mode == "existing" and self.existingIndex < 0:
            return tr("Select a compatible container.")
        return ""

    @Property(bool, notify=changed)
    def valid(self):
        return not self.errorMessage

    def standard_selection(self):
        return InstallationSelection(
            self.suggested, default_environment(self.existing, self.suggested)
        )

    def selection(self):
        if not self.valid:
            raise ValueError(self.errorMessage)
        existing = None
        if self._mode == "default":
            existing = default_environment(self.existing, self.environment)
        elif self._mode == "existing":
            existing = self._candidates(self._mode)[self.existingIndex]
        return InstallationSelection(
            self.environment,
            existing,
            self._name.strip() if self._mode == "custom" else "",
            not self._suggested,
        )

    @Property(str, notify=changed)
    def containerDescription(self):
        if not self.valid:
            return ""
        selection = self.selection()
        name = selection.container_name(self.existing)
        if name == tr("The name will be assigned during setup"):
            return tr("A new container will be created and named during setup.")
        action = tr("reused") if selection.existing else tr("created")
        return tr("Container {0} will be {1} for this installation.").format(name, action)
