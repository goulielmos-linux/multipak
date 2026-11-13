import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

Kirigami.ScrollablePage {
    id: page
    objectName: "androidAdvancedPage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, footerActions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight + topPadding + bottomPadding + actionBar.implicitHeight
    padding: outerMargin
    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2
        BrandHeader { id: header; heading: qsTr("MultiPak Installation Wizard"); showAbout: true; Layout.fillWidth: true; onAboutRequested: page.aboutRequested() }
        PackageSummary { Layout.fillWidth: true }
        Kirigami.Heading { text: qsTr("Advanced settings"); level: 2; Layout.fillWidth: true }
        Kirigami.AbstractCard {
            Layout.fillWidth: true
            contentItem: ColumnLayout {
                spacing: Kirigami.Units.smallSpacing
                Controls.Label { text: qsTr("Container base"); font.bold: true; Layout.fillWidth: true }
                Controls.RadioButton {
                    id: suggestedBase
                    objectName: "androidSuggestedBase"
                    text: qsTr("Use MultiPak's recommendation (%1)").arg(welcome.androidSuggestedVersion)
                    checked: welcome.androidUseSuggested
                    Layout.fillWidth: true
                    onClicked: welcome.chooseAndroidSuggested(true)
                }
                AndroidConfidence {
                    Layout.fillWidth: true
                    Layout.leftMargin: suggestedBase.leftPadding + suggestedBase.indicator.width + suggestedBase.spacing
                }
                Controls.RadioButton {
                    text: qsTr("Choose another Android version")
                    checked: !welcome.androidUseSuggested
                    Layout.fillWidth: true
                    onClicked: welcome.chooseAndroidSuggested(false)
                }
                Controls.ComboBox {
                    objectName: "androidProfileChoice"
                    model: welcome.androidProfiles
                    textRole: "label"; valueRole: "id"
                    enabled: !welcome.androidUseSuggested
                    Layout.fillWidth: true
                    currentIndex: { for (let i=0; i<model.length; i++) if (model[i].id === welcome.androidProfile) return i; return 0; }
                    onActivated: welcome.chooseAndroidProfile(currentValue)
                }
            }
        }
        Kirigami.AbstractCard {
            Layout.fillWidth: true
            contentItem: ColumnLayout {
                spacing: Kirigami.Units.largeSpacing
                Controls.Label { text: qsTr("Container"); font.bold: true; Layout.fillWidth: true }
                Controls.ComboBox {
                    objectName: "androidContainerModeChoice"
                    model: [qsTr("Use the standard MultiPak container"), qsTr("Create a new custom container"), qsTr("Use an existing container")]
                    currentIndex: ["standard", "custom", "existing"].indexOf(welcome.androidContainerMode)
                    Layout.fillWidth: true
                    onActivated: index => welcome.chooseAndroidContainerMode(["standard", "custom", "existing"][index])
                }
                Controls.ComboBox {
                    objectName: "androidEnvironmentChoice"
                    visible: welcome.androidContainerMode === "existing"
                    model: welcome.androidExistingEnvironments
                    textRole: "label"; valueRole: "id"
                    currentIndex: { for (let i=0; i<model.length; i++) if (model[i].id === welcome.androidEnvironment) return i; return -1; }
                    Layout.fillWidth: true
                    onActivated: welcome.chooseAndroidEnvironment(currentValue)
                }
                Controls.TextField {
                    objectName: "androidNewEnvironmentName"
                    visible: welcome.androidContainerMode === "custom"
                    text: welcome.androidNewEnvironmentName
                    placeholderText: qsTr("Container name")
                    maximumLength: 64
                    validator: RegularExpressionValidator { regularExpression: /[^\x00-\x1f\x7f]{0,64}/ }
                    onTextEdited: welcome.setAndroidEnvironmentName(text)
                    Layout.fillWidth: true
                }
                Controls.Label {
                    visible: welcome.androidContainerMode === "custom"
                    text: qsTr("Up to 64 characters. Spaces and accents are allowed.")
                    color: Kirigami.Theme.disabledTextColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Controls.Label {
                    text: welcome.androidContainerDescription
                    visible: text.length > 0
                    textFormat: Text.PlainText; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                Controls.Label {
                    visible: welcome.androidUsesPersonalEnvironment
                    text: welcome.androidExistingVersion
                    Layout.fillWidth: true
                }
                Controls.Label {
                    visible: !welcome.androidSelectionValid && (welcome.androidContainerMode !== "custom"
                        || welcome.androidNewEnvironmentName.trim().length > 0)
                    text: qsTr("Choose an Android version compatible with this application and enter an environment name.")
                    wrapMode: Text.WordWrap; color: Kirigami.Theme.negativeTextColor; Layout.fillWidth: true
                }
            }
        }
        Controls.ToolButton {
            objectName: "androidAlternativeMethod"
            text: qsTr("Show alternative method")
            icon.source: BreezeIcons.source("arrow-down", Kirigami.Theme.backgroundColor)
            enabled: false
            Layout.alignment: Qt.AlignLeft
        }
    }
    footer: Controls.ToolBar {
        id: actionBar
        padding: page.outerMargin
        contentItem: RowLayout {
            id: footerActions
            spacing: Kirigami.Units.smallSpacing
            Item { Layout.fillWidth: true }
            Controls.Button { text: qsTr("Back"); onClicked: page.returnRequested() }
            Controls.Button { text: qsTr("Cancel"); onClicked: page.cancelRequested() }
            Controls.Button { objectName: "androidAdvancedContinue"; text: qsTr("Continue"); highlighted: true; enabled: welcome.androidSelectionValid; onClicked: welcome.confirmAndroidConfiguration() }
        }
    }
}
