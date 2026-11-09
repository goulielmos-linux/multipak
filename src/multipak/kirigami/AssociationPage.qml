import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "associationPage"
    signal aboutRequested()
    signal returnRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + actions.implicitHeight
    padding: outerMargin

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
            text: qsTr("File types")
            level: 2
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
            FormatChoice { formatId: "rpm" }
            FormatChoice { formatId: "deb" }
            FormatChoice { formatId: "arch" }
            FormatChoice { formatId: "appimage" }
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
