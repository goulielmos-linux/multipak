import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

InformationWindow {
    id: dialog
    objectName: "appimageDependenciesDialog"
    property string dependencies
    property string introduction
    property string note: qsTr("The AppImage was analyzed without running it. Some dependencies can only be identified when it is opened.")
    title: qsTr("Missing dependencies")
    preferredWidth: Kirigami.Units.gridUnit * 26
    closeText: qsTr("Close")
    closeObjectName: "appimageDependenciesCloseButton"
    onShown: {
        dependencyText.cursorPosition = 0;
        dependencyText.deselect();
    }

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        Controls.Label {
            visible: text.length > 0
            text: dialog.introduction
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // The same scrolling box as the RPM, DEB and Arch dependency list.
        Controls.ScrollView {
            objectName: "appimageDependenciesScroll"
            clip: true
            contentWidth: availableWidth
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(
                Kirigami.Units.gridUnit * 10,
                dependencyText.implicitHeight + topPadding + bottomPadding
            )
            Controls.ScrollBar.vertical.policy: Controls.ScrollBar.AsNeeded

            Flickable {
                contentWidth: width
                contentHeight: dependencyText.implicitHeight
                boundsBehavior: Flickable.StopAtBounds

                Controls.TextArea.flickable: Controls.TextArea {
                    id: dependencyText
                    objectName: "appimageDependenciesText"
                    // Names come from the package: always plain text.
                    text: dialog.dependencies
                    textFormat: TextEdit.PlainText
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    wrapMode: TextEdit.Wrap
                    Accessible.name: dialog.title
                }
            }
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
