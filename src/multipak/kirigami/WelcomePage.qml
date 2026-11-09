import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: page
    objectName: "welcomePage"
    signal aboutRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, actions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight + outerMargin * 2
    padding: 0

    ColumnLayout {
        id: body
        width: page.width - page.outerMargin * 2
        x: page.outerMargin
        y: page.outerMargin
        spacing: Kirigami.Units.largeSpacing * 2

        BrandHeader {
            id: header
            heading: qsTr("Welcome to the MultiPak Installation Wizard")
            showAbout: true
            Layout.fillWidth: true
            onAboutRequested: page.aboutRequested()
        }

        PackageSummary {
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true

            Kirigami.Icon {
                source: welcome.isAppImage ? "dialog-information" : "dialog-warning"
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.preferredHeight: Kirigami.Units.iconSizes.large
                Layout.alignment: Qt.AlignTop
                Accessible.ignored: true
            }

            Controls.Label {
                objectName: "warningLabel"
                text: welcome.isAppImage
                    ? qsTr("We recommend checking your system's app store for this application before continuing.")
                    : qsTr("The application shown above was built for a different Linux distribution. We recommend checking your system's app store for a compatible version before continuing.")
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignJustify
                Layout.fillWidth: true
            }
        }

        RowLayout {
            id: actions
            Layout.alignment: Qt.AlignRight
            spacing: Kirigami.Units.smallSpacing
            layoutDirection: Qt.RightToLeft

            Controls.Button {
                objectName: "continueButton"
                text: welcome.isAppImage ? qsTr("Continue") : qsTr("Install anyway")
                onClicked: welcome.choose("continue")
            }

            Controls.Button {
                objectName: "searchButton"
                text: qsTr("Search the app store")
                highlighted: true
                Accessible.defaultButton: true
                onClicked: welcome.choose("search")
            }
        }
    }
}
