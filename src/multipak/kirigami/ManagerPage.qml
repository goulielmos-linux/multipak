import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

// The content of Install, Uninstall, Update and Snap services. The header and the
// side bar are in ManagerShell.qml.
Kirigami.ScrollablePage {
    id: page
    objectName: "managerPage"
    signal closeRequested()
    signal adminCommandRequested(string arguments)
    readonly property bool installing: manager.mode === "install" || manager.mode === "home"
    readonly property bool snaps: manager.mode === "services" || manager.mode === "commands"
    readonly property bool listed: manager.mode === "remove" || manager.mode === "update" || snaps
    padding: Kirigami.Units.largeSpacing * 2

    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2

        Kirigami.Heading {
            objectName: "managerHeading"
            text: page.installing ? qsTr("Welcome")
                : manager.mode === "remove" ? qsTr("Uninstall applications")
                : page.snaps ? qsTr("Snap services")
                : qsTr("Update applications")
            level: 2
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // Install: a package file, or a snap from the Snap Store.
        ColumnLayout {
            visible: page.installing
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true
            onVisibleChanged: if (visible) snapNameField.text = ""

            Controls.Label {
                objectName: "snapStoreSiteLink"
                text: qsTr("To install applications, open a package from your file manager or use one of the options below. Snaps from the <a href='https://snapcraft.io/store'>snapcraft.io</a> site also open directly in MultiPak.")
                textFormat: Text.StyledText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                onLinkActivated: link => Qt.openUrlExternally(link)
                HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor }
            }

            Kirigami.AbstractCard {
                Layout.fillWidth: true
                contentItem: ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Controls.Label {
                        text: qsTr("File on this computer")
                        font.bold: true
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Controls.Label {
                        text: qsTr("Choose a package to install.")
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Controls.Button {
                        objectName: "managerChooseFileButton"
                        text: qsTr("Choose file…")
                        enabled: appUpdates.installAllowed && !manager.working
                        onClicked: manager.openInstaller()
                    }
                }
            }

            Kirigami.AbstractCard {
                Layout.fillWidth: true
                contentItem: ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Controls.Label {
                        text: qsTr("Snap Store")
                        font.bold: true
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Controls.Label {
                        text: qsTr("Enter the identifier or the snapcraft.io link of a snap.")
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        Layout.fillWidth: true
                        Controls.TextField {
                            id: snapNameField
                            objectName: "snapNameField"
                            placeholderText: qsTr("For example: hello-world or https://snapcraft.io/hello-world")
                            Layout.fillWidth: true
                            onAccepted: if (snapContinueButton.enabled) snapContinueButton.clicked()
                        }
                        Controls.Button {
                            id: snapContinueButton
                            objectName: "confirmSnapStoreButton"
                            text: qsTr("Continue")
                            enabled: appUpdates.installAllowed && !manager.working
                                && manager.validSnapName(snapNameField.text)
                            onClicked: {
                                manager.installSnap(snapNameField.text);
                                snapNameField.text = "";
                            }
                        }
                    }
                }
            }
        }

        // Checking and updating everything, beside the list they act on.

        Controls.TabBar {
            id: snapTabs
            objectName: "snapTabs"
            visible: page.snaps
            currentIndex: manager.mode === "commands" ? 1 : 0
            // Always shown; without snaps installed through MultiPak, nothing to choose.
            enabled: !manager.working && manager.hasSnaps
            Layout.fillWidth: true
            Controls.TabButton {
                objectName: "snapServicesTab"
                text: qsTr("Background services")
                onClicked: if (manager.mode !== "services") manager.openList("services")
            }
            Controls.TabButton {
                objectName: "snapCommandsTab"
                text: qsTr("Administrator commands")
                onClicked: if (manager.mode !== "commands") manager.openList("commands")
            }
        }

        Kirigami.InlineMessage {
            objectName: "noSnapsMessage"
            visible: page.snaps && !manager.hasSnaps
            text: qsTr("No snaps were installed through MultiPak.")
            type: Kirigami.MessageType.Information
            Layout.fillWidth: true
        }

        Controls.Label {
            visible: manager.mode === "services" && manager.hasSnaps
            text: qsTr("They run with your user account, never as the administrator.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.Label {
            visible: manager.mode === "commands" && manager.hasSnaps
            text: qsTr("They run as root inside the snap's sandbox, never as the administrator of your computer.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.Label {
            visible: manager.mode === "update"
            text: qsTr("Applications installed directly on the system (rpm-ostree) are updated together with the system.")
            color: Kirigami.Theme.disabledTextColor
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.Frame {
            visible: page.listed && manager.rows.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(list.contentHeight + padding * 2,
                Kirigami.Units.gridUnit * 18)
            padding: Kirigami.Units.smallSpacing
            Controls.ScrollView {
                anchors.fill: parent
                contentWidth: availableWidth
                clip: true
                ListView {
                    id: list
                    objectName: "managerList"
                    model: manager.rows
                    currentIndex: manager.selectedIndex
                    enabled: !manager.working
                    keyNavigationEnabled: true
                    boundsBehavior: Flickable.StopAtBounds
                    onCurrentIndexChanged: if (activeFocus) manager.select(currentIndex)
                    delegate: ColumnLayout {
                        id: rowItem
                        required property var modelData
                        required property int index
                        width: ListView.view.width
                        spacing: 0
                        Kirigami.Heading {
                            visible: text.length > 0
                            text: rowItem.modelData.section || ""
                            level: 4
                            leftPadding: Kirigami.Units.largeSpacing
                            topPadding: rowItem.index > 0 ? Kirigami.Units.largeSpacing : Kirigami.Units.smallSpacing
                            bottomPadding: Kirigami.Units.smallSpacing
                            Layout.fillWidth: true
                        }
                        Controls.ItemDelegate {
                            id: rowDelegate
                            readonly property var modelData: rowItem.modelData
                            readonly property int index: rowItem.index
                            readonly property bool updating: manager.mode === "update"
                            objectName: "managerRow" + index
                            Layout.fillWidth: true
                            highlighted: manager.selectedIndex === index
                            padding: Kirigami.Units.largeSpacing
                            onClicked: manager.select(index)
                            contentItem: RowLayout {
                                spacing: Kirigami.Units.largeSpacing
                                Kirigami.Icon {
                                    objectName: "managerApplicationIcon" + index
                                    source: modelData.kind === "container"
                                        ? Qt.resolvedUrl("assets/container.svg") : modelData.icon
                                    Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                                    Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                                    Accessible.ignored: true
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: Kirigami.Units.smallSpacing / 2
                                    Controls.Label {
                                        text: modelData.title
                                        font.bold: true
                                        textFormat: Text.PlainText
                                        wrapMode: Text.Wrap
                                        Layout.fillWidth: true
                                    }
                                    Controls.Label {
                                        text: modelData.subtitle
                                        textFormat: Text.PlainText
                                        wrapMode: Text.Wrap
                                        Layout.fillWidth: true
                                    }
                                    // Where the application lives, such as its container.
                                    Controls.Label {
                                        objectName: "managerRowDetail" + index
                                        visible: text.length > 0
                                        text: modelData.detail
                                        font: Kirigami.Theme.smallFont
                                        color: Kirigami.Theme.disabledTextColor
                                        textFormat: Text.PlainText
                                        wrapMode: Text.Wrap
                                        Layout.fillWidth: true
                                    }
                                    Controls.Label {
                                        visible: !rowDelegate.updating && modelData.note.length > 0
                                        text: modelData.note
                                        color: Kirigami.Theme.neutralTextColor
                                        textFormat: Text.PlainText
                                        wrapMode: Text.Wrap
                                        Layout.fillWidth: true
                                    }
                                }
                                // In the update list, what the check found, beside the button.
                                Controls.Label {
                                    objectName: "managerRowNote" + index
                                    visible: rowDelegate.updating && text.length > 0
                                    text: modelData.note
                                    color: modelData.updatable === true
                                        ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.disabledTextColor
                                    horizontalAlignment: Text.AlignRight
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                    Layout.maximumWidth: Kirigami.Units.gridUnit * 13
                                }
                                Controls.ToolButton {
                                    objectName: "managerRowServiceButton" + index
                                    readonly property bool running: modelData.running === true
                                    visible: manager.mode === "services"
                                    enabled: !manager.working && modelData.supported === true
                                    icon.source: BreezeIcons.source(running ? "material-stop" : "material-play-arrow", Kirigami.Theme.backgroundColor)
                                    text: running ? qsTr("Stop %1").arg(modelData.title) : qsTr("Start %1").arg(modelData.title)
                                    display: Controls.AbstractButton.IconOnly
                                    onClicked: manager.controlServiceRow(index, running ? "stop" : "start")
                                    Controls.ToolTip.visible: hovered
                                    Controls.ToolTip.text: text
                                    Accessible.name: text
                                }
                                Controls.ToolButton {
                                    objectName: "managerRowLogButton" + index
                                    visible: manager.mode === "services"
                                    enabled: !manager.working && modelData.supported === true
                                    icon.source: BreezeIcons.source("material-description", Kirigami.Theme.backgroundColor)
                                    text: qsTr("Show the log of %1").arg(modelData.title)
                                    display: Controls.AbstractButton.IconOnly
                                    onClicked: manager.showServiceLogRow(index)
                                    Controls.ToolTip.visible: hovered
                                    Controls.ToolTip.text: text
                                    Accessible.name: text
                                }
                                Controls.ToolButton {
                                    objectName: "managerRowRemoveButton" + index
                                    visible: manager.mode === "remove"
                                    enabled: !manager.working && modelData.removable === true
                                    icon.source: BreezeIcons.source("material-delete", Kirigami.Theme.backgroundColor)
                                    text: qsTr("Uninstall %1").arg(modelData.title)
                                    display: Controls.AbstractButton.IconOnly
                                    onClicked: manager.removeRow(index)
                                    Controls.ToolTip.visible: hovered
                                    Controls.ToolTip.text: text
                                    Accessible.name: text
                                }
                                Controls.ToolButton {
                                    objectName: "managerRowUpdateButton" + index
                                    visible: manager.mode === "update" && modelData.updatable === true
                                    enabled: !manager.working
                                    icon.source: BreezeIcons.source("material-update", Kirigami.Theme.backgroundColor)
                                    text: qsTr("Update %1").arg(modelData.title)
                                    display: Controls.AbstractButton.IconOnly
                                    onClicked: manager.updateRow(index)
                                    Controls.ToolTip.visible: hovered
                                    Controls.ToolTip.text: text
                                    Accessible.name: text
                                }
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            objectName: "managerUpdateTools"
            visible: page.listed && (manager.mode === "update" || manager.rows.length > 0)
            spacing: Kirigami.Units.smallSpacing
            Layout.fillWidth: true
            Controls.BusyIndicator {
                visible: manager.checkingUpdates
                running: visible
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
            }
            Controls.Label {
                visible: manager.checkingUpdates
                text: qsTr("Checking for updates…")
                color: Kirigami.Theme.disabledTextColor
            }
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "managerRefreshButton"
                visible: manager.mode !== "update"
                text: qsTr("Reload list")
                icon.source: BreezeIcons.source("material-refresh", Kirigami.Theme.backgroundColor)
                enabled: !manager.working
                onClicked: manager.refresh()
            }
            Controls.Button {
                objectName: "managerCheckUpdatesButton"
                visible: manager.mode === "update"
                text: qsTr("Check for updates")
                icon.source: BreezeIcons.source("material-refresh", Kirigami.Theme.backgroundColor)
                enabled: !manager.working
                onClicked: manager.refresh()
            }
            Controls.Button {
                objectName: "managerUpdateAllButton"
                visible: manager.mode === "update"
                text: qsTr("Update all")
                icon.source: BreezeIcons.source("material-update", Kirigami.Theme.backgroundColor)
                enabled: manager.canUpdateAll
                highlighted: true
                onClicked: manager.updateAll()
            }
        }

        RowLayout {
            visible: manager.mode === "commands" && manager.rows.length > 0
            spacing: Kirigami.Units.smallSpacing
            Layout.fillWidth: true
            onVisibleChanged: if (visible) adminArgumentsField.text = ""
            Controls.Label {
                text: manager.adminCommandName || qsTr("Command")
                textFormat: Text.PlainText
            }
            Controls.TextField {
                id: adminArgumentsField
                objectName: "adminArgumentsField"
                placeholderText: qsTr("Arguments, for example: status")
                enabled: !manager.working
                Layout.fillWidth: true
                onAccepted: if (adminRunButton.enabled) adminRunButton.clicked()
            }
            Controls.Button {
                id: adminRunButton
                objectName: "adminRunButton"
                text: qsTr("Run…")
                icon.source: BreezeIcons.source("dialog-warning", Kirigami.Theme.backgroundColor)
                enabled: manager.canAct && manager.validAdminArguments(adminArgumentsField.text)
                onClicked: page.adminCommandRequested(adminArgumentsField.text)
            }
        }

        Controls.Button {
            objectName: "managerRevertSnapButton"
            visible: manager.hasPreviousSnap
            enabled: !manager.working
            text: qsTr("Revert to previous version")
            Layout.alignment: Qt.AlignRight
            onClicked: manager.revertSelectedSnap()
        }

        Controls.ProgressBar {
            objectName: "managerProgress"
            visible: manager.busy
            indeterminate: manager.progress < 0
            from: 0
            to: 100
            value: Math.max(0, manager.progress)
            Layout.fillWidth: true
        }

        Controls.Label {
            objectName: "managerStatus"
            text: manager.status
            visible: text.length > 0
            color: manager.hasError ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Controls.ToolButton {
            objectName: "managerDetailsButton"
            visible: manager.logs.length > 0
            text: details.visible ? qsTr("Hide details") : qsTr("Show details")
            onClicked: details.expanded = !details.expanded
        }

        LogView {
            id: details
            objectName: "managerLogView"
            logObjectName: "managerLog"
            text: manager.logs
            property bool expanded: false
            visible: expanded && manager.logs.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.gridUnit * 8
        }
    }

    footer: Controls.ToolBar {
        id: footerBar
        padding: page.padding
        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "managerCloseButton"
                text: qsTr("Close")
                enabled: manager.canClose
                onClicked: page.closeRequested()
            }
        }
    }
}
