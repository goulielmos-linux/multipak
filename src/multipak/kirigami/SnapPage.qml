import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

// Review page for a snap. It follows the layout of the container review for RPM, DEB
// and Arch packages: the environment, then the application below it.
Kirigami.ScrollablePage {
    id: page
    objectName: "snapPage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
    readonly property bool refused: welcome.snapRefusal.length > 0
    readonly property bool switching: welcome.state === "switching"
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, actions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + actionBar.implicitHeight
    padding: outerMargin

    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2

        BrandHeader {
            id: header
            heading: qsTr("MultiPak Installation Wizard")
            showAbout: true
            Layout.fillWidth: true
            onAboutRequested: page.aboutRequested()
        }

        PackageSummary {
            Layout.fillWidth: true
        }

        // As a store's app page: the channel to install from, the stable one first.
        RowLayout {
            objectName: "snapChannelRow"
            visible: welcome.snapChannels.length > 1
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true
            Controls.Label {
                text: qsTr("Channel:")
            }
            Controls.ComboBox {
                id: channelBox
                objectName: "snapChannelBox"
                popup.height: Math.min(channelBox.popup.implicitHeight, Kirigami.Units.gridUnit * 9)
                model: welcome.snapChannels
                textRole: "label"
                valueRole: "name"
                currentIndex: welcome.snapChannelIndex
                enabled: !page.switching
                onActivated: welcome.chooseSnapChannel(currentValue)
                Layout.fillWidth: true

                // Choosing an item breaks the binding; the channel shown is the package's.
                Connections {
                    target: welcome
                    function onDataChanged() { if (!page.switching) channelBox.currentIndex = welcome.snapChannelIndex; }
                    function onStateChanged() { if (!page.switching) channelBox.currentIndex = welcome.snapChannelIndex; }
                }

                // Breeze's menu normally enables scrolling only when it exceeds the window.
                Binding {
                    target: channelBox.popup.contentItem
                    property: "interactive"
                    when: target instanceof Flickable
                    value: when && target.contentHeight > target.height
                }
            }
        }

        ColumnLayout {
            objectName: "snapChannelProgress"
            visible: page.switching
            spacing: Kirigami.Units.smallSpacing
            Layout.fillWidth: true
            Controls.Label {
                text: welcome.status
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            Controls.ProgressBar {
                indeterminate: welcome.progress < 0
                from: 0
                to: 100
                value: Math.max(0, welcome.progress)
                Layout.fillWidth: true
            }
        }

        Kirigami.Heading {
            text: page.refused ? qsTr("This snap cannot be installed") : qsTr("Ready to install")
            level: 2
            Layout.fillWidth: true
        }

        Kirigami.InlineMessage {
            objectName: "snapRefusal"
            visible: page.refused
            text: welcome.snapRefusal
            type: Kirigami.MessageType.Error
            Layout.fillWidth: true
        }

        // Always shown, not folded into the alerts: other computers can connect to it.
        Kirigami.InlineMessage {
            objectName: "snapNetworkWarning"
            visible: !page.refused && welcome.snapNetworkWarning.length > 0
            text: welcome.snapNetworkWarning
            type: Kirigami.MessageType.Warning
            Layout.fillWidth: true
        }

        Kirigami.AbstractCard {
            id: alertsCard
            objectName: "snapAlerts"
            property bool expanded: false
            visible: !page.refused && welcome.snapAlerts.length > 0
            Layout.fillWidth: true

            contentItem: ColumnLayout {
                spacing: Kirigami.Units.largeSpacing

                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true

                    Kirigami.Icon {
                        source: BreezeIcons.source("dialog-warning", Kirigami.Theme.backgroundColor)
                        Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                        Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                        Accessible.ignored: true
                    }
                    Kirigami.Heading {
                        text: qsTr("Alerts")
                        level: 3
                        Layout.fillWidth: true
                    }
                    Controls.ToolButton {
                        objectName: "snapAlertsToggle"
                        text: alertsCard.expanded ? qsTr("Collapse") : qsTr("Expand")
                        icon.name: alertsCard.expanded ? "arrow-up" : "arrow-down"
                        LayoutMirroring.enabled: true
                        onClicked: alertsCard.expanded = !alertsCard.expanded
                    }
                }

                Repeater {
                    model: alertsCard.expanded ? welcome.snapAlerts : []

                    delegate: ColumnLayout {
                        required property var modelData
                        spacing: Kirigami.Units.smallSpacing
                        Layout.fillWidth: true

                        Controls.Label {
                            text: modelData.title
                            font.bold: true
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                        Controls.Label {
                            visible: text.length > 0
                            text: modelData.text
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                        Repeater {
                            model: modelData.items
                            delegate: RowLayout {
                                required property string modelData
                                spacing: Kirigami.Units.smallSpacing
                                Layout.leftMargin: Kirigami.Units.largeSpacing
                                Layout.fillWidth: true
                                Controls.Label { text: "•"; Accessible.ignored: true }
                                Controls.Label {
                                    text: modelData
                                    textFormat: Text.PlainText
                                    font.family: "monospace"
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }
                        }
                        Controls.Label {
                            visible: text.length > 0
                            text: modelData.note
                            textFormat: Text.PlainText
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        Kirigami.AbstractCard {
            objectName: "snapEnvironmentCard"
            visible: !page.refused
            Layout.fillWidth: true

            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing

                Kirigami.Icon {
                    objectName: "snapEnvironmentIcon"
                    // The bubblewrap environment snaps run in.
                    source: Qt.resolvedUrl("assets/sandbox.svg")
                    Layout.preferredWidth: Kirigami.Units.iconSizes.large
                    Layout.preferredHeight: Kirigami.Units.iconSizes.large
                    Layout.alignment: Qt.AlignTop
                    Accessible.ignored: true
                }

                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true

                    RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        Layout.fillWidth: true

                        Controls.Label {
                            objectName: "snapEnvironmentTitle"
                            // Classic snaps also run in bubblewrap, over the whole
                            // system; the explanation below tells them apart.
                            text: qsTr("Bubblewrap based on %1").arg(welcome.snapBaseTitle)
                            font.bold: true
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: false
                        }

                        Item {
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        Layout.fillWidth: true

                        Controls.Label {
                            objectName: "snapConfidencePrefix"
                            text: qsTr("Distribution detection confidence:")
                        }

                        Controls.Label {
                            objectName: "snapConfidenceValue"
                            // The snap declares its base itself.
                            text: qsTr("High")
                            font.bold: true
                            color: Kirigami.Theme.positiveTextColor
                        }

                        Controls.ToolButton {
                            objectName: "snapEnvironmentInfoButton"
                            icon.source: BreezeIcons.source("help-about", Kirigami.Theme.backgroundColor)
                            icon.width: Kirigami.Units.iconSizes.small
                            icon.height: Kirigami.Units.iconSizes.small
                            padding: Kirigami.Units.smallSpacing / 2
                            implicitWidth: icon.width + leftPadding + rightPadding
                            implicitHeight: icon.height + topPadding + bottomPadding
                            text: qsTr("About the recommended environment")
                            display: Controls.AbstractButton.IconOnly
                            onClicked: environmentDialog.open()
                            Controls.ToolTip.visible: hovered
                            Controls.ToolTip.text: text
                            Accessible.name: text
                        }

                        Item {
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        // The base is the environment itself, shown above; only content
                        // snaps the app uses on top of it are listed here.
                        objectName: "snapRequirementsRow"
                        visible: welcome.snapRequirementsText !== ""
                        spacing: Kirigami.Units.smallSpacing
                        Layout.fillWidth: true

                        Kirigami.Icon {
                            source: Qt.resolvedUrl("assets/dependencies.svg")
                            Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                            Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                            Accessible.ignored: true
                        }

                        Controls.Label {
                            text: qsTr("Required dependencies:")
                        }

                        Controls.Label {
                            objectName: "snapRequirementsLink"
                            text: qsTr("<a href='dependencies'>View full list</a>")
                            textFormat: Text.RichText
                            linkColor: Kirigami.Theme.linkColor
                            Accessible.role: Accessible.Link
                            Accessible.name: qsTr("View the full list of required dependencies")
                            activeFocusOnTab: true
                            onLinkActivated: requirementsDialog.open()
                            Keys.onReturnPressed: requirementsDialog.open()
                            Keys.onSpacePressed: requirementsDialog.open()
                            HoverHandler {
                                cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor
                            }
                        }

                        Item {
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        RowLayout {
            visible: !page.refused
            Layout.fillWidth: true
            spacing: 0

            Item {
                id: applicationBranch
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.fillHeight: true
                Accessible.ignored: true

                Rectangle {
                    x: applicationBranch.width / 2
                    y: -body.spacing
                    width: 1
                    height: applicationBranch.height / 2 + body.spacing
                    color: Kirigami.Theme.disabledTextColor
                }

                Rectangle {
                    x: applicationBranch.width / 2
                    y: applicationBranch.height / 2
                    width: applicationBranch.width / 2
                    height: 1
                    color: Kirigami.Theme.disabledTextColor
                }
            }

            Kirigami.AbstractCard {
                objectName: "snapApplicationCard"
                Layout.fillWidth: true

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Image {
                        source: welcome.applicationIcon
                        sourceSize.width: Kirigami.Units.iconSizes.large
                        sourceSize.height: Kirigami.Units.iconSizes.large
                        fillMode: Image.PreserveAspectFit
                        Layout.preferredWidth: Kirigami.Units.iconSizes.large
                        Layout.preferredHeight: Kirigami.Units.iconSizes.large
                        Accessible.ignored: true
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.smallSpacing

                        Controls.Label {
                            text: qsTr("Application")
                            font.bold: true
                            Layout.fillWidth: true
                        }

                        Controls.Label {
                            objectName: "snapInstallationApplication"
                            text: welcome.applicationNameHtml + " — " + welcome.versionHtml
                            textFormat: Text.RichText
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        ColumnLayout {
            objectName: "snapComponents"
            visible: !page.refused && welcome.snapComponents.length > 0
            spacing: Kirigami.Units.smallSpacing
            Layout.fillWidth: true

            Controls.Label {
                text: qsTr("Optional components:")
                font.bold: true
                Layout.fillWidth: true
            }

            Repeater {
                model: welcome.snapComponents
                delegate: Controls.CheckBox {
                    required property var modelData
                    required property int index
                    objectName: "snapComponent" + index
                    // Names and summaries come from the package: plain text only.
                    text: modelData.name
                        + (modelData.summary.length > 0 ? " — " + modelData.summary : "")
                        + (modelData.available ? "" : " " + qsTr("(not available)"))
                    checked: modelData.selected
                    enabled: modelData.available
                    onToggled: welcome.setSnapComponent(modelData.name, checked)
                    Layout.fillWidth: true
                    contentItem: Controls.Label {
                        text: parent.text
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        leftPadding: parent.indicator.width + parent.spacing
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }

        Controls.Label {
            objectName: "snapExplanation"
            visible: !page.refused
            text: welcome.snapClassic
                ? qsTr("The package and its dependencies will be installed in an environment created with bubblewrap. As a classic snap, the application sees the whole system there, with your user's permissions: bubblewrap only adds the /snap folder, with the snap and the %1 it needs, without isolating it. The base system image is not changed.\n\nAny available shortcuts will appear in your application launcher.").arg(welcome.snapBaseTitle)
                : qsTr("The package and its dependencies will be installed in an environment created with bubblewrap, without changing the base system image.\n\nAny available shortcuts will appear in your application launcher.")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

    }

    InformationWindow {
        id: environmentDialog
        objectName: "snapEnvironmentDialog"
        transientParent: page.Window.window
        title: qsTr("Suggested environment")
        preferredWidth: Kirigami.Units.gridUnit * 28

        Controls.Label {
            objectName: "snapEnvironmentExplanation"
            text: welcome.snapEnvironmentExplanation
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    AppImageDependenciesDialog {
        id: requirementsDialog
        objectName: "snapRequirementsDialog"
        transientParent: page.Window.window
        title: qsTr("Required dependencies")
        dependencies: welcome.snapRequirementsText
        note: qsTr("MultiPak will download from the Snap Store the dependencies it does not already have.")
    }

    footer: Controls.ToolBar {
        id: actionBar
        padding: page.outerMargin
        contentItem: RowLayout {
            id: actions
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "snapBackButton"
                text: qsTr("Back")
                enabled: !page.switching
                onClicked: page.returnRequested()
            }
            Controls.Button {
                objectName: "snapCancelButton"
                text: qsTr("Cancel")
                onClicked: page.switching ? welcome.abandon() : page.cancelRequested()
            }
            Controls.Button {
                objectName: "snapInstallButton"
                text: qsTr("Install")
                highlighted: true
                enabled: !page.refused && !page.switching
                onClicked: welcome.requestInstallation()
            }
        }
    }
}
