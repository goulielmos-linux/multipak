import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "languagePage"
    signal aboutRequested()
    signal returnRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + actions.implicitHeight
    padding: outerMargin

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
            objectName: "languageHeading"
            text: qsTr("Language")
            level: 2
            Layout.fillWidth: true
        }
        Controls.Label {
            text: qsTr("Choose the language used by MultiPak.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Controls.ComboBox {
            objectName: "languageCombo"
            model: languages.options
            textRole: "label"
            currentIndex: languages.selectedIndex
            onActivated: index => languages.choose(index)
            Accessible.name: qsTr("MultiPak language")
            Layout.fillWidth: true
        }
        Controls.Label {
            text: qsTr("The system language follows your desktop session settings. English is used if no translation is available.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Controls.Label {
            objectName: "languageStatus"
            text: languages.status
            visible: text.length > 0
            textFormat: Text.PlainText
            color: languages.hasError ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
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
                objectName: "languageBackButton"
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }
            Controls.Button {
                objectName: "languageSaveButton"
                text: qsTr("Save")
                highlighted: true
                onClicked: languages.save(manager.previewMode)
            }
        }
    }
}
