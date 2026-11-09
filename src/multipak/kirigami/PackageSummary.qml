import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

ColumnLayout {
    spacing: Kirigami.Units.smallSpacing

    Controls.Label {
        objectName: "applicationLabel"
        text: qsTr("Application: <b>%1</b> (%2)").arg(welcome.applicationNameHtml).arg(welcome.versionHtml)
        textFormat: Text.RichText
        wrapMode: Text.Wrap
        Layout.fillWidth: true
    }

    Controls.Label {
        objectName: "packagingLabel"
        text: qsTr("Package format: <b>%1</b>").arg(welcome.packaging)
        textFormat: Text.RichText
        Layout.fillWidth: true
    }
}
