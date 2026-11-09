import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

RowLayout {
    id: header
    property string heading
    property bool showAbout: false
    property url logoSource: "assets/multipak-installer.svg"
    property string aboutText: qsTr("About the Application Installer — MultiPak")
    signal aboutRequested()

    spacing: Kirigami.Units.largeSpacing

    Image {
        source: header.logoSource
        sourceSize.width: Kirigami.Units.iconSizes.large
        sourceSize.height: Kirigami.Units.iconSizes.large
        Layout.preferredWidth: Kirigami.Units.iconSizes.large
        Layout.preferredHeight: Kirigami.Units.iconSizes.large
        fillMode: Image.PreserveAspectFit
    }

    RowLayout {
        spacing: 0
        Kirigami.Heading {
            text: header.heading
            level: 1
            wrapMode: Text.WordWrap
        }
        Controls.ToolButton {
            objectName: "aboutButton"
            visible: header.showAbout
            icon.name: "help-about"
            text: header.aboutText
            display: Controls.AbstractButton.IconOnly
            onClicked: header.aboutRequested()
            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: text
            Accessible.name: text
        }
    }

    Item {
        Layout.fillWidth: true
    }
}
