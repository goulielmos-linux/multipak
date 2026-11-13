import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

InformationWindow {
    id: dialog
    objectName: "appimageDependenciesDialog"
    property string dependencies
    property string note: qsTr("Note: this list was obtained without running the application and may be incomplete.")
    title: qsTr("Missing dependencies")
    preferredWidth: Kirigami.Units.gridUnit * 26
    closeText: qsTr("Close")
    closeObjectName: "appimageDependenciesCloseButton"

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        Controls.Label {
            objectName: "appimageDependenciesText"
            // Names come from the AppImage: always plain text.
            text: dialog.dependencies
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.Label {
            text: dialog.note
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            opacity: 0.7
            Layout.fillWidth: true
        }
    }
}
