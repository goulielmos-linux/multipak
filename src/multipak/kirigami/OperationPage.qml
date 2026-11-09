import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "operationPage"
    signal aboutRequested()
    property bool detailsExpanded: welcome.state === "error"
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, footerActions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + operationFooter.implicitHeight
    padding: outerMargin

    Connections {
        target: welcome
        function onStateChanged() {
            if (welcome.state === "error") {
                page.detailsExpanded = true;
            }
        }
    }

    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2

        BrandHeader {
            id: header
            heading: welcome.state === "loading"
                ? qsTr("Welcome to the MultiPak Installation Wizard")
                : qsTr("MultiPak Installation Wizard")
            showAbout: true
            Layout.fillWidth: true
            onAboutRequested: page.aboutRequested()
        }

        Kirigami.Heading {
            objectName: "operationTitle"
            visible: welcome.state !== "loading"
            text: welcome.state === "searching" ? qsTr("Search the app store")
                : welcome.state === "error" ? qsTr("Could not complete the operation")
                : welcome.state === "cleaning" ? qsTr("Cancelling installation")
                : qsTr("Installation in progress")
            level: 2
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.Label {
            objectName: "operationStatus"
            text: welcome.status
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Controls.ProgressBar {
            objectName: "installationProgress"
            visible: welcome.state !== "error"
            indeterminate: welcome.progress < 0
            from: 0
            to: 100
            value: Math.max(0, welcome.progress)
            Layout.fillWidth: true
        }

        Controls.ToolButton {
            objectName: "operationDetailsButton"
            visible: welcome.logs.length > 0
            text: page.detailsExpanded ? qsTr("Hide details") : qsTr("Show details")
            onClicked: page.detailsExpanded = !page.detailsExpanded
        }

        LogView {
            objectName: "operationLogView"
            logObjectName: "operationLog"
            text: page.detailsExpanded ? welcome.logs : ""
            visible: page.detailsExpanded && welcome.logs.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.gridUnit * 9
        }
    }

    footer: Controls.ToolBar {
        id: operationFooter
        padding: page.outerMargin
        contentItem: RowLayout {
            id: footerActions
            spacing: Kirigami.Units.smallSpacing
            Item {
                Layout.fillWidth: true
            }
            Controls.Button {
                objectName: "operationBackButton"
                text: qsTr("Back")
                visible: welcome.canReturn
                onClicked: welcome.returnToWelcome()
            }
            Controls.Button {
                objectName: "operationCancelButton"
                text: welcome.state === "error"
                    ? (welcome.selectedMethod === "system" ? qsTr("Close") : qsTr("Cancel installation"))
                    : qsTr("Cancel")
                visible: welcome.canCancel || welcome.state === "error"
                onClicked: welcome.abandon()
            }
            Controls.Button {
                objectName: "operationRetryButton"
                text: qsTr("Try again")
                visible: welcome.canRetry
                highlighted: true
                onClicked: welcome.retry()
            }
        }
    }
}
