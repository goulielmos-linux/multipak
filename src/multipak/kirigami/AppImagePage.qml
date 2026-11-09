import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "appimagePage"
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
        PackageSummary { Layout.fillWidth: true }
        Kirigami.Heading {
            text: qsTr("Ready to install")
            level: 2
            Layout.fillWidth: true
        }
        Kirigami.AbstractCard {
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
                }
                Controls.Label {
                    text: welcome.applicationNameHtml
                    textFormat: Text.RichText
                    font.bold: true
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }
        Controls.Label {
            text: qsTr("A copy of the AppImage will be saved and a shortcut added to your application launcher.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
    footer: Controls.ToolBar {
        id: actionBar
        padding: page.outerMargin
        contentItem: RowLayout {
            id: actions
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "appimageBackButton"
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }
            Controls.Button {
                objectName: "appimageCancelButton"
                text: qsTr("Cancel")
                onClicked: page.cancelRequested()
            }
            Controls.Button {
                objectName: "appimageInstallButton"
                text: qsTr("Install")
                highlighted: true
                onClicked: welcome.requestInstallation()
            }
        }
    }
}
