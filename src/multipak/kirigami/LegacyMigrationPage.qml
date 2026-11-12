import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "legacyMigrationPage"
    signal closeRequested()
    padding: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Kirigami.Units.gridUnit * 33
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + actions.implicitHeight

    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2

        Kirigami.Heading {
            text: qsTr("Remove the old MultiPak")
            level: 2
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Controls.Label {
            text: qsTr("An older MultiPak with a different Flatpak ID is still installed. It must be removed to avoid duplicate shortcuts and packages opening in the old version.")
            visible: legacyMigration.error.length === 0
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Controls.Label {
            text: legacyMigration.error
            textFormat: Text.PlainText
            visible: text.length > 0
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }

    footer: Controls.ToolBar {
        id: actions
        padding: page.padding
        contentItem: RowLayout {
            Controls.BusyIndicator {
                visible: legacyMigration.checking
                running: visible
                Layout.preferredHeight: closeButton.implicitHeight
                Layout.preferredWidth: closeButton.implicitHeight
            }
            Item { Layout.fillWidth: true }
            Controls.Button {
                id: closeButton
                text: qsTr("Close")
                onClicked: page.closeRequested()
            }
            Controls.Button {
                objectName: "legacyRemoveButton"
                text: legacyMigration.error.length > 0 ? qsTr("Try again") : qsTr("Remove")
                enabled: !legacyMigration.checking
                highlighted: true
                onClicked: legacyMigration.remove()
            }
        }
    }
}
