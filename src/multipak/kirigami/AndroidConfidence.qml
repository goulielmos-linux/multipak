import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons
RowLayout {
    id: confidenceRow
    Layout.minimumHeight: implicitHeight
    Layout.maximumHeight: implicitHeight
    spacing: Kirigami.Units.smallSpacing
    Controls.Label { text: qsTr("Identification confidence:") }
    Controls.Label {
        text: welcome.androidConfidence
        font.bold: true
        color: text === qsTr("High") ? Kirigami.Theme.positiveTextColor : text === qsTr("Medium") ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.negativeTextColor
    }
    Controls.ToolButton {
        objectName: "androidConfidenceInfo"
        icon.source: BreezeIcons.source("help-about", Kirigami.Theme.backgroundColor)
        icon.width: Kirigami.Units.iconSizes.small
        icon.height: Kirigami.Units.iconSizes.small
        padding: Kirigami.Units.smallSpacing / 2
        implicitWidth: icon.width + leftPadding + rightPadding
        implicitHeight: icon.height + topPadding + bottomPadding
        Layout.minimumHeight: implicitHeight
        Layout.maximumHeight: implicitHeight
        text: qsTr("About the recommended environment")
        display: Controls.AbstractButton.IconOnly
        onClicked: explanation.open()
        Controls.ToolTip.visible: hovered
        Controls.ToolTip.text: welcome.androidConfidenceExplanation
    }
    Item { Layout.fillWidth: true }
    InformationWindow {
        id: explanation
        objectName: "androidConfidenceDialog"
        transientParent: confidenceRow.Window.window
        title: qsTr("About the recommended environment")
        Controls.Label {
            objectName: "androidConfidenceExplanation"
            Layout.fillWidth: true
            text: welcome.androidConfidenceExplanation
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
        }
    }
}
