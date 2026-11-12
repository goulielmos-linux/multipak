import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons
Kirigami.AbstractCard {
    property string title
    property string description
    property string iconName
    property string buttonName
    signal activated()
    id: card
    contentItem: RowLayout {
        spacing: Kirigami.Units.largeSpacing
        Kirigami.Icon { source: BreezeIcons.source(card.iconName, Kirigami.Theme.backgroundColor); Layout.preferredWidth: Kirigami.Units.iconSizes.medium; Layout.preferredHeight: Kirigami.Units.iconSizes.medium; Accessible.ignored: true }
        ColumnLayout {
            Layout.fillWidth: true
            Controls.Label { text: card.title; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Controls.Label { text: card.description; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        }
        Controls.Button { objectName: card.buttonName; text: qsTr("Select"); Accessible.name: card.title; onClicked: card.activated() }
    }
}
