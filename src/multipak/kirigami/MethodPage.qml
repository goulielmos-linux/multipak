import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "methodPage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
    property string selectedMode: welcome.selectedMode
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, footerActions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + methodFooter.implicitHeight
    padding: outerMargin

    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2

        BrandHeader {
            id: header
            heading: qsTr("MultiPak Installation Wizard")
            showAbout: true
            Layout.fillWidth: true
            onAboutRequested: page.aboutRequested()
        }

        PackageSummary {
            Layout.fillWidth: true
        }

        Kirigami.Heading {
            text: qsTr("Installation method and settings")
            level: 2
            Layout.fillWidth: true
        }

        MethodCard {
            objectName: "standardCard"
            choiceObjectName: "standardMode"
            heading: qsTr("Default settings (recommended)")
            description: qsTr("Installs the application safely and automatically.")
            checked: page.selectedMode === "standard"
            Layout.fillWidth: true
            onSelectionRequested: {
                page.selectedMode = "standard";
            }
        }

        MethodCard {
            objectName: "advancedCard"
            choiceObjectName: "advancedMode"
            heading: qsTr("Advanced settings")
            description: qsTr("Customize the settings or choose a different installation method.")
            checked: page.selectedMode === "advanced"
            Layout.fillWidth: true
            onSelectionRequested: {
                page.selectedMode = "advanced";
            }
        }

    }

    footer: Controls.ToolBar {
        id: methodFooter
        padding: page.outerMargin

        contentItem: RowLayout {
            id: footerActions
            spacing: Kirigami.Units.smallSpacing

            Item {
                Layout.fillWidth: true
            }

            Controls.Button {
                objectName: "methodBackButton"
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }

            Controls.Button {
                objectName: "methodCancelButton"
                text: qsTr("Cancel")
                onClicked: page.cancelRequested()
            }

            Controls.Button {
                objectName: "methodContinueButton"
                text: qsTr("Continue")
                highlighted: true
                Accessible.defaultButton: true
                onClicked: welcome.chooseConfiguration(page.selectedMode)
            }
        }
    }
}
