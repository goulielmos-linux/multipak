import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "androidPage"
    signal aboutRequested()
    signal cancelRequested()
    signal returnRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, footerActions.implicitWidth, Kirigami.Units.gridUnit * 29
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
        PackageSummary { Layout.fillWidth: true }
        Controls.Label {
            visible: !welcome.androidSelectionValid
            text: qsTr("Choose an Android version compatible with this application and enter an environment name.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Kirigami.Heading {
            text: qsTr("Ready to install")
            level: 2
            Layout.fillWidth: true
        }
        Kirigami.AbstractCard {
            objectName: "androidEnvironmentCard"
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
                        text: qsTr("Container based on %1").arg(welcome.androidSelectedVersion)
                        font.bold: true
                        Layout.fillWidth: true
                    }
                    AndroidConfidence { Layout.fillWidth: true }
                    Controls.Label {
                        objectName: "androidEnvironmentStatus"
                        text: welcome.androidContainerDescription
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    RowLayout {
                        objectName: "androidTranslationDependency"
                        visible: welcome.androidTranslationState !== "native"
                        spacing: Kirigami.Units.smallSpacing
                        Layout.fillWidth: true
                        Kirigami.Icon {
                            source: Qt.resolvedUrl("assets/dependencies.svg")
                            Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                            Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                            Accessible.ignored: true
                        }
                        Controls.Label { text: qsTr("Additional dependencies:") }
                        Controls.Label {
                            objectName: "androidTranslationLink"
                            text: qsTr("<a href='translation'>ARM translation layer</a>")
                            textFormat: Text.RichText
                            linkColor: Kirigami.Theme.linkColor
                            Accessible.role: Accessible.Link
                            Accessible.name: qsTr("ARM translation layer")
                            activeFocusOnTab: true
                            onLinkActivated: translationDialog.open()
                            Keys.onReturnPressed: translationDialog.open()
                            Keys.onSpacePressed: translationDialog.open()
                            HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Controls.Label {
                        objectName: "androidTranslationStatus"
                        visible: welcome.androidTranslationState === "unsupported"
                        text: welcome.androidTranslationExplanation
                        color: Kirigami.Theme.negativeTextColor
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
        }
        InstallationBranch {
            cardName: "androidApplicationCard"
            Layout.fillWidth: true
            iconSource: welcome.applicationIcon
            Controls.Label {
                text: qsTr("Application")
                font.bold: true
                Layout.fillWidth: true
            }
            Controls.Label {
                objectName: "androidInstallationApplication"
                text: welcome.applicationNameHtml + " — " + welcome.versionHtml
                textFormat: Text.RichText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
        Controls.Label {
            objectName: "protectedInstallationExplanation"
            text: qsTr("The application and its bundled data will be installed in an Android container using Waydroid, without changing the base system image.\n\nAny available shortcuts will appear in your application launcher.")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
    InformationWindow {
        id: translationDialog
        objectName: "androidTranslationDialog"
        transientParent: page.Window.window
        title: qsTr("ARM translation layer")
        Controls.Label {
            text: welcome.androidTranslationDetails
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
    footer: Controls.ToolBar {
        id: actionBar
        padding: page.outerMargin
        contentItem: RowLayout {
            id: footerActions
            spacing: Kirigami.Units.smallSpacing
            Item { Layout.fillWidth: true }
            Controls.Button {
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }
            Controls.Button {
                text: qsTr("Cancel")
                onClicked: page.cancelRequested()
            }
            Controls.Button {
                objectName: "androidInstallButton"
                text: qsTr("Install")
                highlighted: true
                enabled: welcome.androidSelectionValid && welcome.androidTranslationState !== "unsupported"
                onClicked: welcome.requestInstallation()
            }
        }
    }
}
