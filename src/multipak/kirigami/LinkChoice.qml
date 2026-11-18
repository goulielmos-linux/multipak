import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Controls.ApplicationWindow {
    id: root
    objectName: "linkChoiceWindow"
    visible: true
    color: Kirigami.Theme.backgroundColor
    title: qsTr("Open with — MultiPak")
    width: Kirigami.Units.gridUnit * 24
    height: content.implicitHeight + footer.implicitHeight + Kirigami.Units.largeSpacing * 2
    minimumWidth: Kirigami.Units.gridUnit * 18

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Kirigami.Units.largeSpacing
        spacing: Kirigami.Units.largeSpacing
        Kirigami.Heading {
            level: 2
            text: qsTr("Open this address with:")
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        Controls.Label {
            text: linkChoice.address
            elide: Text.ElideMiddle
            opacity: 0.7
            Layout.fillWidth: true
        }
        Repeater {
            model: linkChoice.programs
            Controls.Button {
                required property int index
                required property var modelData
                objectName: "linkChoiceProgram" + index
                text: modelData.name
                icon.name: modelData.icon.startsWith("/") ? "" : modelData.icon
                icon.source: modelData.icon.startsWith("/") ? "file://" + modelData.icon : ""
                Layout.fillWidth: true
                onClicked: if (linkChoice.open(index)) root.close()
            }
        }
        Controls.Label {
            visible: linkChoice.programs.length === 0
            text: qsTr("No program opens this address.")
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        Controls.Label {
            visible: text.length > 0
            text: linkChoice.error
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
    footer: Controls.DialogButtonBox {
        id: footer
        Controls.Button {
            text: qsTr("Cancel")
            onClicked: root.close()
            Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.RejectRole
        }
    }
}
