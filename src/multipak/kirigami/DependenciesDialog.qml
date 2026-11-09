import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

InformationWindow {
    id: dialog
    objectName: "dependenciesDialog"
    property string dependencies
    readonly property bool hasDependencies: dependencies.trim().length > 0
    title: qsTr("Dependencies")
    preferredWidth: Kirigami.Units.gridUnit * 28
    closeText: qsTr("Close")
    closeObjectName: "dependenciesCloseButton"
    onShown: {
        dependencyText.cursorPosition = 0;
        dependencyText.deselect();
    }

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        Controls.Label {
            objectName: "dependenciesDescription"
            visible: !dialog.hasDependencies
            text: qsTr("The package declares no dependencies.")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.ScrollView {
            id: dependencyList
            objectName: "dependenciesScroll"
            visible: dialog.hasDependencies
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
                    objectName: "dependenciesText"
                    text: dialog.dependencies
                    textFormat: TextEdit.PlainText
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    wrapMode: TextEdit.Wrap
                    Accessible.name: qsTr("List of dependencies declared by the package")
                }
            }
        }
    }

}
