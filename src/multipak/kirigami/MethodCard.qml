import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

Kirigami.AbstractCard {
    id: card
    property string heading
    property string description
    property string choiceObjectName
    property bool warning: false
    property int warningCount: 1
    signal selectionRequested()

    checkable: false
    showClickFeedback: true
    onClicked: selectionRequested()
    Accessible.name: heading

    contentItem: ColumnLayout {
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing

            Controls.RadioButton {
                id: methodChoice
                objectName: card.choiceObjectName
                text: card.heading
                font.bold: true
                checked: card.checked
                onClicked: card.selectionRequested()

                Binding {
                    target: methodChoice.contentItem
                    property: "color"
                    when: card.warning
                    value: Kirigami.Theme.negativeTextColor
                    restoreMode: Binding.RestoreBindingOrValue
                }
            }

            RowLayout {
                visible: card.warning
                spacing: Kirigami.Units.smallSpacing
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("This option changes the system and requires a restart.")

                Repeater {
                    model: card.warning ? card.warningCount : 0
                    delegate: Kirigami.Icon {
                        source: BreezeIcons.source("dialog-warning", Kirigami.Theme.backgroundColor)
                        Layout.preferredWidth: Kirigami.Units.iconSizes.small
                        Layout.preferredHeight: Kirigami.Units.iconSizes.small
                        Accessible.ignored: true
                    }
                }
            }

            Item {
                Layout.fillWidth: true
            }
        }

        Controls.Label {
            text: card.description
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
