import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

// The Application Manager: the header, a side bar and the chosen tool beside it.
Kirigami.Page {
    id: shell
    objectName: "managerShell"
    signal aboutRequested()
    signal closeRequested()
    signal adminCommandRequested(string arguments)
    // install, remove, update, waydroid, snaps, associations or language
    property string section: "install"
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real sidebarWidth: Kirigami.Units.gridUnit * 12
    readonly property real preferredWindowWidth: Math.max(header.implicitWidth + outerMargin * 2,
        sidebarWidth + Kirigami.Units.gridUnit * 34)
    readonly property real preferredWindowHeight: Kirigami.Units.gridUnit * 36
    readonly property bool navigable: !manager.working && !manager.android.busy
    padding: 0

    function open(name) {
        if (name === "about") {
            shell.aboutRequested();
            return;
        }
        if (!navigable)
            return;
        // First, so that the signals the calls below emit see the new section.
        shell.section = name;
        if (name === "install") {
            manager.openInstall();
            // While MultiPak must be updated, the page explains why nothing can be installed.
            if (manager.mode !== "install")
                manager.goHome();
        } else if (name === "remove" || name === "update") {
            manager.openList(name);
        } else if (name === "snaps") {
            manager.openList("services");
        } else if (name === "waydroid") {
            manager.goHome();
            manager.openAndroidEnvironments();
        } else if (name === "associations") {
            manager.openAssociations();
        } else if (name === "language") {
            manager.openLanguages();
        }
    }

    Component.onCompleted: {
        open("install");
        // Reads the package lists the containers already have; nothing is blocked.
        manager.checkUpdatesInBackground();
    }

    Connections {
        target: manager
        function onAndroidEnvironmentsRequested() { shell.section = "waydroid"; }
        function onAssociationSettingsRequested() {
            languages.reset();
            shell.section = "associations";
        }
        function onLanguageSettingsRequested() {
            languages.reset();
            shell.section = "language";
        }
        function onChanged() {
            // A list opened elsewhere, such as from the command line, selects its item.
            const mode = manager.mode;
            if (mode === "remove" || mode === "update" || mode === "install")
                shell.section = mode;
            else if (mode === "services" || mode === "commands")
                shell.section = "snaps";
        }
    }

    component NavItem: Controls.ItemDelegate {
        id: navItem
        required property string key
        required property string iconName
        property int count: 0
        objectName: "managerNav_" + key
        Layout.fillWidth: true
        highlighted: shell.section === key
        enabled: shell.navigable || highlighted
        icon.source: BreezeIcons.source(iconName, Kirigami.Theme.backgroundColor)
        onClicked: shell.open(key)
        Accessible.role: Accessible.PageTab
        Accessible.name: count > 0 ? qsTr("%1, %2 updates").arg(text).arg(count) : text
        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing * 2
            Kirigami.Icon {
                source: navItem.icon.source
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                Accessible.ignored: true
            }
            Controls.Label {
                text: navItem.text
                color: navItem.highlighted ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Controls.Label {
                objectName: "managerNavCount_" + navItem.key
                visible: navItem.count > 0
                text: navItem.count
                font.bold: true
                color: navItem.highlighted ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.highlightColor
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true
            Layout.margins: shell.outerMargin
            // As in the About window: more room above the title than below the subtitle.
            Layout.topMargin: shell.outerMargin + Kirigami.Units.smallSpacing
            Layout.bottomMargin: Kirigami.Units.largeSpacing + Kirigami.Units.smallSpacing

            BrandHeader {
                id: header
                heading: qsTr("MultiPak Application Manager")
                logoSource: "assets/multipak-manager.svg"
                aboutText: qsTr("About the Application Manager — MultiPak")
                subtitle: qsTr("Manage your applications and set your preferences.")
                Layout.fillWidth: true
                onAboutRequested: shell.aboutRequested()
            }

            Kirigami.InlineMessage {
                visible: appUpdates.required
                text: qsTr("Update MultiPak to continue installing applications.")
                type: Kirigami.MessageType.Warning
                Layout.fillWidth: true
                actions: Kirigami.Action {
                    text: qsTr("Update")
                    onTriggered: appUpdates.openStore()
                }
            }

            // AppImages whose container was renamed or deleted, one message each.
            Repeater {
                model: manager.repairs
                delegate: Kirigami.InlineMessage {
                    required property var modelData
                    required property int index
                    objectName: "managerRepairMessage" + index
                    visible: true
                    // The name is escaped by the manager; the bold tag makes it rich text.
                    text: qsTr("%1 needs to be repaired.").arg(modelData.html)
                    type: Kirigami.MessageType.Warning
                    Layout.fillWidth: true
                    actions: [
                        Kirigami.Action {
                            text: qsTr("Delete")
                            enabled: !manager.busy
                            onTriggered: manager.requestDelete(modelData.id)
                        },
                        Kirigami.Action {
                            text: qsTr("Repair")
                            enabled: !manager.busy
                            onTriggered: manager.requestRepair(modelData.id)
                        }
                    ]
                }
            }
        }

        Kirigami.Separator { Layout.fillWidth: true }

        RowLayout {
            spacing: 0
            Layout.fillWidth: true
            Layout.fillHeight: true

            Rectangle {
                color: Kirigami.Theme.alternateBackgroundColor
                Layout.preferredWidth: shell.sidebarWidth
                Layout.fillHeight: true

                ColumnLayout {
                    objectName: "managerSidebar"
                    anchors.fill: parent
                    anchors.margins: Kirigami.Units.smallSpacing
                    spacing: 0

                    NavItem { key: "install"; iconName: "list-add"; text: qsTr("Install") }
                    NavItem { key: "remove"; iconName: "trash-empty"; text: qsTr("Uninstall") }
                    NavItem {
                        key: "update"
                        iconName: "system-software-update"
                        text: qsTr("Update")
                        count: manager.updateCount
                    }
                    Kirigami.Separator {
                        Layout.fillWidth: true
                        Layout.topMargin: Kirigami.Units.smallSpacing
                        Layout.bottomMargin: Kirigami.Units.smallSpacing
                    }
                    NavItem { key: "waydroid"; iconName: "waydroid"; text: qsTr("Waydroid integration") }
                    NavItem {
                        key: "snaps"
                        iconName: "folder-snap-symbolic"
                        text: qsTr("Snap services")
                    }
                    Kirigami.Separator {
                        Layout.fillWidth: true
                        Layout.topMargin: Kirigami.Units.smallSpacing
                        Layout.bottomMargin: Kirigami.Units.smallSpacing
                    }
                    NavItem { key: "associations"; iconName: "document-open"; text: qsTr("File associations") }
                    NavItem { key: "language"; iconName: "languages"; text: qsTr("Language") }
                    Kirigami.Separator {
                        Layout.fillWidth: true
                        Layout.topMargin: Kirigami.Units.smallSpacing
                        Layout.bottomMargin: Kirigami.Units.smallSpacing
                    }
                    NavItem { key: "about"; iconName: "help-about"; text: qsTr("About") }
                    Item { Layout.fillHeight: true }
                }
            }

            Kirigami.Separator { Layout.fillHeight: true }

            StackLayout {
                objectName: "managerContent"
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: shell.section === "waydroid" ? 1
                    : shell.section === "associations" ? 2
                    : shell.section === "language" ? 3
                    : 0

                ManagerPage {
                    onCloseRequested: shell.closeRequested()
                    onAdminCommandRequested: arguments => shell.adminCommandRequested(arguments)
                }
                AndroidEnvironmentsPage {
                    embedded: true
                    onAboutRequested: shell.aboutRequested()
                }
                AssociationPage {
                    embedded: true
                    onAboutRequested: shell.aboutRequested()
                }
                LanguagePage {
                    embedded: true
                    onAboutRequested: shell.aboutRequested()
                }
            }
        }
    }
}
