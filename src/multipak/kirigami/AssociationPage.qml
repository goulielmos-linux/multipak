import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "managerSettingsPage"
    signal aboutRequested()
    signal returnRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + actions.implicitHeight
    padding: outerMargin

    Connections {
        target: manager
        function onAssociationsSaved() { languages.save(manager.previewMode); }
    }

    component FormatChoice: Controls.CheckBox {
        id: choice
        required property string formatId
        readonly property var entry: manager.formatOptions.find(option => option.id === formatId)
        objectName: "association_" + formatId
        text: entry ? entry.label : ""
        checked: entry ? entry.selected : false
        enabled: !manager.busy && manager.associationsReady
        onClicked: manager.setAssociation(formatId, checked)
        contentItem: Controls.Label {
            text: choice.text
            font: choice.font
            wrapMode: Text.WordWrap
            verticalAlignment: Text.AlignVCenter
            leftPadding: !choice.mirrored && choice.indicator
                ? choice.indicator.width + choice.spacing : 0
            rightPadding: choice.mirrored && choice.indicator
                ? choice.indicator.width + choice.spacing : 0
        }
        Layout.fillWidth: true
    }

    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2
        BrandHeader {
            id: header
            heading: qsTr("MultiPak Application Manager")
            logoSource: "assets/multipak-manager.svg"
            showAbout: true
            Layout.fillWidth: true
            onAboutRequested: page.aboutRequested()
        }
        Kirigami.Heading {
            text: qsTr("MultiPak settings")
            level: 2
            Layout.fillWidth: true
        }
        Kirigami.Heading {
            text: qsTr("File types")
            level: 3
            Layout.fillWidth: true
        }
        Controls.Label {
            text: qsTr("Choose which file types should open in the MultiPak Installation Wizard.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ColumnLayout {
            spacing: Kirigami.Units.smallSpacing
            Layout.fillWidth: true
            Repeater {
                objectName: "associationChoices"
                model: manager.associationsReady ? manager.formatOptions : []
                delegate: FormatChoice {
                    required property var modelData
                    formatId: modelData.id
                }
            }
        }
        Kirigami.Separator { Layout.fillWidth: true }
        Kirigami.Heading {
            objectName: "languageHeading"
            text: qsTr("Language")
            level: 3
            Layout.fillWidth: true
        }
        Controls.ComboBox {
            objectName: "languageCombo"
            model: languages.options
            textRole: "label"
            currentIndex: languages.selectedIndex
            onActivated: index => languages.choose(index)
            enabled: !manager.busy
            Accessible.name: qsTr("MultiPak language")
            Layout.fillWidth: true
        }
        Controls.Label {
            objectName: "languageFallbackNotice"
            text: qsTr("English is used if no translation is available.")
            visible: languages.selectedIndex === 0
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Controls.Label {
            objectName: "languageStatus"
            text: languages.status
            visible: languages.hasError && text.length > 0
            textFormat: Text.PlainText
            color: languages.hasError ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Controls.ProgressBar {
            visible: manager.busy
            indeterminate: true
            Layout.fillWidth: true
        }
        Controls.Label {
            objectName: "associationStatus"
            text: manager.status
            visible: text.length > 0
            color: manager.hasError ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
    footer: Controls.ToolBar {
        id: actions
        padding: page.outerMargin
        contentItem: RowLayout {
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "associationBackButton"
                text: qsTr("Back")
                enabled: !manager.busy
                onClicked: page.returnRequested()
            }
            Controls.Button {
                objectName: "associationSaveButton"
                text: qsTr("Save")
                enabled: !manager.busy && manager.associationsReady
                highlighted: true
                onClicked: manager.saveAssociations()
            }
        }
    }
}
