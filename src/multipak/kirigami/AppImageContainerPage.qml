import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

// Review page for an AppImage that runs with its dependencies from MultiPak's container.
// The AppImage itself stays in MultiPak's folder: only its dependencies go into the
// container, so the two cards are linked side by side instead of nested.
Kirigami.ScrollablePage {
    id: page
    objectName: "appimageContainerPage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
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

        Kirigami.Heading {
            text: qsTr("Ready to install")
            level: 2
            Layout.fillWidth: true
        }

        ColumnLayout {
            spacing: 0
            Layout.fillWidth: true

            Kirigami.AbstractCard {
                id: containerCard
                objectName: "appimageContainerCard"
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

                        RowLayout {
                            spacing: Kirigami.Units.smallSpacing
                            Layout.fillWidth: true

                            Controls.Label {
                                objectName: "appimageEnvironmentTitle"
                                text: qsTr("Container based on %1").arg(welcome.appImageEnvironmentTitle)
                                font.bold: true
                                textFormat: Text.PlainText
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: false
                            }

                            Controls.ToolButton {
                                objectName: "appimageEnvironmentInfoButton"
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

                        Controls.Label {
                            objectName: "appimageContainerName"
                            text: (welcome.appImageContainerExists
                                ? qsTr("Container %1 will be reused for the dependencies.")
                                : qsTr("Container %1 will be created for the dependencies."))
                                .arg(welcome.appImageContainerName)
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
                                // Found without running the app: the isolated check
                                // looks for the rest before the installation finishes.
                                text: qsTr("Preliminary dependencies:")
                            }

                            Controls.Label {
                                objectName: "appimageRequirementsLink"
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

            // A straight link between the two icons: the application uses the container,
            // it is not inside it.
            Item {
                objectName: "appimageContainerLink"
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.largeSpacing * 2
                Accessible.ignored: true

                Rectangle {
                    x: containerCard.leftPadding + Kirigami.Units.iconSizes.large / 2
                    width: 1
                    height: parent.height
                    color: Kirigami.Theme.disabledTextColor
                }
            }

            Kirigami.AbstractCard {
                objectName: "appimageApplicationCard"
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
                            objectName: "appimageInstallationApplication"
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
            objectName: "appimageContainerExplanation"
            text: qsTr("The dependencies will be installed in a Distrobox container using %1, without changing the base system image. The application will open from this container.\n\nAny available shortcuts will appear in your application launcher.").arg("DNF")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    InformationWindow {
        id: environmentDialog
        objectName: "appimageEnvironmentDialog"
        transientParent: page.Window.window
        title: qsTr("Suggested environment")
        preferredWidth: Kirigami.Units.gridUnit * 28

        Controls.Label {
            objectName: "appimageEnvironmentExplanation"
            text: welcome.appImageEnvironmentExplanation
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    AppImageDependenciesDialog {
        id: requirementsDialog
        objectName: "appimageRequirementsDialog"
        transientParent: page.Window.window
        title: qsTr("Preliminary dependencies")
        introduction: qsTr("To run the AppImage in the container:")
        dependencies: welcome.appImageRequirementsText
        note: qsTr("Before the installation finishes, the application will run for %1 seconds in an isolated environment to look for other dependencies.").arg(welcome.appImageCheckSeconds)
            + " " + qsTr("The check runs without access to your files or to the network.")
    }

    footer: Controls.ToolBar {
        id: actionBar
        padding: page.outerMargin
        contentItem: RowLayout {
            id: actions
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "appimageContainerBackButton"
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }
            Controls.Button {
                objectName: "appimageContainerCancelButton"
                text: qsTr("Cancel")
                onClicked: page.cancelRequested()
            }
            Controls.Button {
                objectName: "appimageContainerInstallButton"
                text: qsTr("Install")
                highlighted: true
                onClicked: welcome.requestInstallation()
            }
        }
    }
}
