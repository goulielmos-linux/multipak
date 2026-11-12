import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

InformationWindow {
    id: dialog
    objectName: "distroboxSetupDialog"
    title: welcome.distroboxRestartRequired ? qsTr("Restart required")
        : welcome.distroboxCanInstall ? qsTr("Install Distrobox") : "Distrobox"
    preferredWidth: Kirigami.Units.gridUnit * 30

    Controls.Label {
        objectName: "distroboxSetupMessage"
        text: welcome.distroboxMessage
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }

    footer: Controls.ToolBar {
        padding: dialog.contentPadding
        contentItem: RowLayout {
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "distroboxSetupCloseButton"
                text: welcome.distroboxRestartRequired ? qsTr("Restart later")
                    : welcome.distroboxCanInstall ? qsTr("Cancel") : qsTr("Close")
                onClicked: dialog.close()
            }
            Controls.Button {
                objectName: "distroboxSetupInstallButton"
                text: qsTr("Install Distrobox")
                visible: welcome.distroboxCanInstall
                onClicked: {
                    dialog.close();
                    welcome.requestDistroboxInstallation();
                }
            }
            Controls.Button {
                objectName: "distroboxSetupRestartButton"
                text: qsTr("Restart now")
                visible: welcome.distroboxRestartRequired
                onClicked: {
                    dialog.close();
                    welcome.requestDistroboxRestart();
                }
            }
        }
    }
}
