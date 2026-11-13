import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

Kirigami.ScrollablePage {
    id: page
    objectName: "managerPage"
    signal aboutRequested()
    signal closeRequested()
    signal settingsRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + footerBar.implicitHeight
    padding: outerMargin

    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2

        BrandHeader {
            id: header
            heading: qsTr("MultiPak Application Manager")
            logoSource: "assets/multipak-manager.svg"
            aboutText: qsTr("About the Application Manager — MultiPak")
            showAbout: true
            Layout.fillWidth: true
            onAboutRequested: page.aboutRequested()
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

        Controls.Label {
            visible: manager.mode === "home"
            text: qsTr("Manage your applications and set your preferences.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        ColumnLayout {
            visible: manager.mode === "home"
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true

            ManagerActionCard {
                title: qsTr("Install applications")
                description: qsTr("Choose a package to install.")
                iconName: "list-add"
                enabled: appUpdates.installAllowed
                buttonName: "managerInstallButton"
                onActivated: manager.openInstaller()
                Layout.fillWidth: true
            }

            Kirigami.AbstractCard {
                Layout.fillWidth: true
                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing
                    Kirigami.Icon {
                        source: BreezeIcons.source("edit-delete", Kirigami.Theme.backgroundColor)
                        Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                        Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                        Accessible.ignored: true
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Controls.Label {
                            text: qsTr("Uninstall applications")
                            font.bold: true
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Controls.Label {
                            text: qsTr("Remove applications installed through MultiPak.")
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                    Controls.Button {
                        objectName: "managerRemoveButton"
                        text: qsTr("Select")
                        Accessible.name: qsTr("Select an application to uninstall")
                        onClicked: manager.openList("remove")
                    }
                }
            }

            Kirigami.AbstractCard {
                Layout.fillWidth: true
                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing
                    Kirigami.Icon {
                        source: BreezeIcons.source("system-software-update", Kirigami.Theme.backgroundColor)
                        Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                        Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                        Accessible.ignored: true
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Controls.Label {
                            text: qsTr("Update applications")
                            font.bold: true
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Controls.Label {
                            text: qsTr("Choose a MultiPak container to update.")
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                    Controls.Button {
                        objectName: "managerUpdateButton"
                        text: qsTr("Select")
                        Accessible.name: qsTr("Select a container to update")
                        onClicked: manager.openList("update")
                    }
                }
            }
            ManagerActionCard {
                title: qsTr("Android containers")
                description: qsTr("Create and manage separate Android containers.")
                iconName: "phone"
                buttonName: "managerAndroidEnvironmentsButton"
                onActivated: manager.openAndroidEnvironments()
                Layout.fillWidth: true
            }
            ManagerActionCard {
                title: qsTr("MultiPak settings")
                description: qsTr("Configure file types and the interface language.")
                iconName: "configure"
                buttonName: "managerSettingsButton"
                onActivated: page.settingsRequested()
                Layout.fillWidth: true
            }

        }

        Kirigami.Heading {
            visible: manager.mode !== "home"
            text: manager.mode === "remove" ? qsTr("Uninstall applications") : qsTr("Update applications")
            level: 2
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.Label {
            visible: manager.mode === "update"
            text: qsTr("Choose a container to update everything installed in it, including applications and their dependencies. Your system itself is not changed.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.Frame {
            visible: manager.mode !== "home" && manager.rows.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(list.contentHeight + padding * 2,
                Kirigami.Units.gridUnit * 16)
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
                    enabled: !manager.busy
                    keyNavigationEnabled: true
                    boundsBehavior: Flickable.StopAtBounds
                    onCurrentIndexChanged: if (activeFocus) manager.select(currentIndex)
                    delegate: Controls.ItemDelegate {
                        required property var modelData
                        required property int index
                        objectName: "managerRow" + index
                        width: ListView.view.width
                        highlighted: manager.selectedIndex === index
                        padding: Kirigami.Units.largeSpacing
                        onClicked: manager.select(index)
                        contentItem: RowLayout {
                            spacing: Kirigami.Units.largeSpacing
                            Kirigami.Icon {
                                objectName: "managerApplicationIcon" + index
                                source: manager.mode === "remove"
                                    ? modelData.icon : Qt.resolvedUrl("assets/container.svg")
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
                                Controls.Label {
                                    text: modelData.detail
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                }
                                Controls.Label {
                                    visible: modelData.note.length > 0
                                    text: modelData.note
                                    color: Kirigami.Theme.neutralTextColor
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }
            }
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
            Controls.ToolButton {
                objectName: "managerRefreshButton"
                visible: manager.mode !== "home"
                text: qsTr("Reload list")
                icon.source: BreezeIcons.source("view-refresh", Kirigami.Theme.backgroundColor)
                enabled: !manager.busy
                onClicked: manager.refresh()
            }
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "managerBackButton"
                text: qsTr("Back")
                visible: manager.mode !== "home"
                enabled: !manager.busy
                onClicked: manager.goHome()
            }
            Controls.Button {
                objectName: "managerCloseButton"
                text: qsTr("Close")
                enabled: manager.canClose
                onClicked: page.closeRequested()
            }
            Controls.Button {
                objectName: "managerActionButton"
                text: manager.mode === "remove" ? qsTr("Uninstall…") : qsTr("Update")
                visible: manager.mode !== "home"
                enabled: manager.canAct
                highlighted: true
                onClicked: manager.mode === "remove"
                    ? manager.requestRemoval() : manager.updateSelected()
            }
        }
    }
}
