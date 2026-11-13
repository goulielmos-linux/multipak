import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

InformationWindow {
    id: dialog
    objectName: "applicationUpdateDialog"
    property bool installer: false
    signal installerCloseRequested()
    title: "MultiPak"
    preferredWidth: Kirigami.Units.gridUnit * 34
    function dismiss() { dialog.close(); }
    onClosing: {
        if (appUpdates.required && installer)
            Qt.callLater(installerCloseRequested);
    }
    Connections {
        target: appUpdates
        function onOffered() { dialog.open(); }
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.largeSpacing * 2
        Kirigami.Icon {
            source: appUpdates.required
                ? BreezeIcons.source("security-medium", Kirigami.Theme.backgroundColor)
                : Qt.resolvedUrl("assets/multipak.svg")
            Layout.preferredWidth: Kirigami.Units.iconSizes.huge
            Layout.preferredHeight: Kirigami.Units.iconSizes.huge
            Accessible.ignored: true
        }
        Kirigami.Heading {
            objectName: "updateNoticeHeading"
            text: appUpdates.required ? qsTr("Security update required") : qsTr("Update available")
            level: 2
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
    Controls.Label {
        text: appUpdates.required
            ? qsTr("This version of MultiPak has a security issue. Update to continue installing applications.")
            : qsTr("A new version of MultiPak is available.")
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    Controls.Label {
        objectName: "updateNoticeVersions"
        text: qsTr("Installed version: %1 → New version: %2").arg(appUpdates.currentVersion).arg(appUpdates.latestVersion)
        color: Kirigami.Theme.disabledTextColor
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    Controls.Label {
        text: appUpdates.error
        visible: text.length > 0
        color: Kirigami.Theme.negativeTextColor
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    footer: Controls.ToolBar {
        padding: dialog.contentPadding
        contentItem: RowLayout {
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "updateRemindButton"
                text: appUpdates.required ? qsTr("Close") : qsTr("Remind me later")
                onClicked: {
                    if (appUpdates.required || appUpdates.remindLater())
                        dialog.dismiss();
                }
            }
            Controls.Button {
                objectName: "updateNowButton"
                text: qsTr("Update")
                highlighted: true
                onClicked: appUpdates.openStore()
            }
        }
    }
}
