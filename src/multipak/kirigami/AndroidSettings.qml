import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Controls.ApplicationWindow {
    id: root
    objectName: "androidSettingsWindow"
    visible: true
    color: Kirigami.Theme.backgroundColor
    title: qsTr("Android application settings — MultiPak")
    width: Kirigami.Units.gridUnit * 28
    height: Math.min(Screen.desktopAvailableHeight - 60,
        content.implicitHeight + footer.implicitHeight + Kirigami.Units.largeSpacing * 2)
    minimumWidth: Kirigami.Units.gridUnit * 24
    onClosing: event => { if (androidSettings.busy) event.accepted = false; }

    Controls.ScrollView {
        anchors.fill: parent
        anchors.margins: Kirigami.Units.largeSpacing
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            id: content
            width: parent.width
            spacing: Kirigami.Units.largeSpacing
            Kirigami.Heading {
                level: 2
                text: androidSettings.applicationName
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            Controls.Label {
                text: androidSettings.containerName
                opacity: 0.7
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            Controls.CheckBox {
                id: touch
                objectName: "androidMouseTouch"
                text: qsTr("Use the mouse as touch input")
                checked: androidSettings.touch
                enabled: androidSettings.ready && !androidSettings.busy
            }
            Controls.CheckBox {
                id: keyboard
                objectName: "androidVirtualKeyboard"
                text: qsTr("Show the virtual keyboard")
                checked: androidSettings.keyboard
                enabled: androidSettings.ready && !androidSettings.busy
                Layout.fillWidth: true
            }
            Controls.CheckBox {
                id: arrows
                objectName: "androidArrowGestures"
                text: qsTr("Use arrow keys as swipe gestures")
                checked: androidSettings.arrows
                enabled: androidSettings.ready && !androidSettings.busy
            }
            Controls.BusyIndicator {
                visible: androidSettings.busy
                running: visible
                Layout.alignment: Qt.AlignHCenter
            }
            Controls.Label {
                objectName: "androidInputStatus"
                visible: text.length > 0
                text: androidSettings.status
                color: androidSettings.hasError ? Kirigami.Theme.negativeTextColor
                    : Kirigami.Theme.textColor
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }
    }
    footer: Controls.DialogButtonBox {
        id: footer
        background: Rectangle {
            color: Kirigami.Theme.backgroundColor
            Rectangle { width: parent.width; height: 1; color: Kirigami.Theme.disabledTextColor }
        }
        Controls.Button {
            text: qsTr("Reopen application…")
            enabled: androidSettings.ready && !androidSettings.busy
            onClicked: reopenDialog.open()
            Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.ActionRole
        }
        Controls.Button {
            text: androidSettings.ready ? qsTr("Apply") : qsTr("Try again")
            enabled: !androidSettings.busy
            highlighted: true
            onClicked: androidSettings.ready
                ? androidSettings.save(touch.checked, keyboard.checked, arrows.checked) : androidSettings.refresh()
            Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.ApplyRole
        }
        Controls.Button {
            text: qsTr("Close")
            enabled: !androidSettings.busy
            onClicked: root.close()
            Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.RejectRole
        }
    }
    Controls.Dialog {
        id: reopenDialog
        title: qsTr("Reopen this application?")
        anchors.centerIn: parent
        width: root.width - Kirigami.Units.largeSpacing * 4
        modal: true
        standardButtons: Controls.Dialog.Ok | Controls.Dialog.Cancel
        contentItem: Controls.Label {
            text: qsTr("Only this application will close and open again. Unsaved progress may be lost. Apply any pending changes first. The system may ask for authorization.")
            wrapMode: Text.Wrap
        }
        onAccepted: androidSettings.reopen()
    }
}
