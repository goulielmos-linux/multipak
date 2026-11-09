import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "environmentPage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, footerActions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + environmentFooter.implicitHeight
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

        Kirigami.Heading {
            text: qsTr("Ready to install")
            level: 2
            Layout.fillWidth: true
        }

        Kirigami.AbstractCard {
            objectName: "containerCard"
            Layout.fillWidth: true

            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing

                Kirigami.Icon {
                    source: Qt.resolvedUrl("assets/container.svg")
                    Layout.preferredWidth: Kirigami.Units.iconSizes.large
                    Layout.preferredHeight: Kirigami.Units.iconSizes.large
                    Layout.alignment: Qt.AlignTop
                    Accessible.ignored: true
                }

                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true

                    Controls.Label {
                        objectName: "environmentTitle"
                        text: qsTr("Container based on %1").arg(welcome.environmentTitle)
                        font.bold: true
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        Layout.fillWidth: true
                        visible: !welcome.environmentManuallySelected

                        Controls.Label {
                            objectName: "confidencePrefix"
                            text: qsTr("Distribution detection confidence:")
                        }

                        Controls.Label {
                            objectName: "confidenceValue"
                            text: welcome.confidence
                            font.bold: true
                            color: welcome.confidence === qsTr("High")
                                ? Kirigami.Theme.positiveTextColor
                                : welcome.confidence === qsTr("Medium")
                                    ? Kirigami.Theme.neutralTextColor
                                    : Kirigami.Theme.negativeTextColor
                        }

                        Controls.ToolButton {
                            objectName: "suggestionInfoButton"
                            icon.name: "help-about"
                            icon.width: Kirigami.Units.iconSizes.small
                            icon.height: Kirigami.Units.iconSizes.small
                            padding: Kirigami.Units.smallSpacing / 2
                            implicitWidth: icon.width + leftPadding + rightPadding
                            implicitHeight: icon.height + topPadding + bottomPadding
                            text: qsTr("About the recommended environment")
                            display: Controls.AbstractButton.IconOnly
                            onClicked: suggestionDialog.open()
                            Controls.ToolTip.visible: hovered
                            Controls.ToolTip.text: text
                            Accessible.name: text
                        }

                        Item {
                            Layout.fillWidth: true
                        }
                    }

                    Controls.Label {
                        objectName: "manualBaseDescription"
                        text: qsTr("Selected base image")
                        visible: welcome.environmentManuallySelected
                        Layout.fillWidth: true
                    }

                    Controls.Label {
                        objectName: "selectedContainerName"
                        text: (welcome.containerExists
                            ? qsTr("Container %1 will be reused for this installation.")
                            : qsTr("Container %1 will be created for this installation."))
                            .arg(welcome.containerName)
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }

                    RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        Layout.fillWidth: true

                        Kirigami.Icon {
                            source: Qt.resolvedUrl("assets/dependencies.svg")
                            Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                            Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                            Accessible.ignored: true
                        }

                        Controls.Label {
                            text: qsTr("Additional dependencies:")
                        }

                        Controls.Label {
                            objectName: "dependenciesLink"
                            text: qsTr("<a href='dependencies'>View full list</a>")
                            textFormat: Text.RichText
                            linkColor: Kirigami.Theme.linkColor
                            Accessible.role: Accessible.Link
                            Accessible.name: qsTr("View the full list of additional dependencies")
                            activeFocusOnTab: true
                            onLinkActivated: dependenciesDialog.open()
                            Keys.onReturnPressed: dependenciesDialog.open()
                            Keys.onSpacePressed: dependenciesDialog.open()
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
                objectName: "applicationCard"
                Layout.fillWidth: true

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Kirigami.Icon {
                        source: Qt.resolvedUrl("assets/application.svg")
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
                            objectName: "installationApplication"
                            text: welcome.applicationNameHtml + " — " + welcome.versionHtml
                            textFormat: Text.RichText
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        Controls.Label {
            objectName: "protectedInstallationExplanation"
            text: qsTr("The package and its dependencies will be installed in a Distrobox container using %1, without changing the base system image.\n\nAny available shortcuts will appear in your application launcher.").arg(welcome.packageManager)
                + (welcome.packaging === "Arch Linux"
                    ? qsTr(" Before installation, the container packages will be updated by Pacman.")
                    : "")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    footer: Controls.ToolBar {
        id: environmentFooter
        padding: page.outerMargin

        contentItem: RowLayout {
            id: footerActions
            spacing: Kirigami.Units.smallSpacing

            Item {
                Layout.fillWidth: true
            }

            Controls.Button {
                objectName: "environmentBackButton"
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }

            Controls.Button {
                objectName: "environmentCancelButton"
                text: qsTr("Cancel")
                onClicked: page.cancelRequested()
            }

            Controls.Button {
                objectName: "environmentInstallButton"
                text: qsTr("Install")
                highlighted: true
                Accessible.defaultButton: true
                onClicked: welcome.requestInstallation()
            }
        }
    }

    InformationWindow {
        id: suggestionDialog
        objectName: "suggestionDialog"
        transientParent: page.Window.window
        title: qsTr("Suggested environment")
        preferredWidth: Kirigami.Units.gridUnit * 28

        Controls.Label {
            objectName: "suggestionExplanation"
            text: welcome.confidenceExplanation
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    DependenciesDialog {
        id: dependenciesDialog
        transientParent: page.Window.window
        dependencies: welcome.dependencies
    }
}
