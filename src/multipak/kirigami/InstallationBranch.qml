import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

RowLayout {
    id: branch
    default property alias contents: column.data
    property alias cardName: card.objectName
    property alias iconSource: icon.source
    property bool continues: false
    spacing: 0

    Item {
        id: connector
        Layout.preferredWidth: Kirigami.Units.iconSizes.large
        Layout.fillHeight: true
        Accessible.ignored: true
        Rectangle {
            x: connector.width / 2
            y: -Kirigami.Units.largeSpacing * 2
            width: 1
            height: (branch.continues ? connector.height : connector.height / 2)
                + Kirigami.Units.largeSpacing * 2
            color: Kirigami.Theme.disabledTextColor
        }
        Rectangle {
            x: connector.width / 2
            y: connector.height / 2
            width: connector.width / 2
            height: 1
            color: Kirigami.Theme.disabledTextColor
        }
    }
    Kirigami.AbstractCard {
        id: card
        Layout.fillWidth: true
        contentItem: RowLayout {
            spacing: Kirigami.Units.largeSpacing
            Kirigami.Icon {
                id: icon
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.preferredHeight: Kirigami.Units.iconSizes.large
                Layout.alignment: Qt.AlignTop
                Accessible.ignored: true
            }
            ColumnLayout {
                id: column
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
            }
        }
    }
}
