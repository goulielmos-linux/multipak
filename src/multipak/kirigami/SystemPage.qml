import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

Kirigami.ScrollablePage {
    id: page
    objectName: "systemPage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, footerActions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + systemFooter.implicitHeight
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
            text: qsTr("Install directly on your system")
            level: 2
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true

            Kirigami.Icon {
                source: BreezeIcons.source("dialog-warning", Kirigami.Theme.backgroundColor)
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.preferredHeight: Kirigami.Units.iconSizes.large
                Layout.alignment: Qt.AlignTop
                Accessible.ignored: true
            }

            Controls.Label {
                text: qsTr("Changes operating system files. This can cause dependency conflicts or prevent applications from working after an update.")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        Controls.Label {
            text: qsTr("The package and its dependencies will be layered onto the base system image using rpm-ostree. This creates a new deployment that takes effect after you restart.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.Label {
            visible: !welcome.systemInstallAvailable
            text: qsTr("Direct installation is unavailable. It requires a system booted with rpm-ostree.")
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.CheckBox {
            id: accepted
            objectName: "systemAcknowledgement"
            text: qsTr("I understand the risks and want to continue.")
            enabled: welcome.systemInstallAvailable
            Layout.fillWidth: true
        }
    }

    footer: Controls.ToolBar {
        id: systemFooter
        padding: page.outerMargin

        contentItem: RowLayout {
            id: footerActions
            spacing: Kirigami.Units.smallSpacing

            Item {
                Layout.fillWidth: true
            }

            Controls.Button {
                objectName: "systemBackButton"
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }

            Controls.Button {
                objectName: "systemCancelButton"
                text: qsTr("Cancel")
                onClicked: page.cancelRequested()
            }

            Controls.Button {
                objectName: "systemInstallButton"
                text: qsTr("Install")
                highlighted: true
                enabled: accepted.checked && welcome.systemInstallAvailable
                onClicked: welcome.requestSystemInstallation(accepted.checked)
            }
        }
    }
}
