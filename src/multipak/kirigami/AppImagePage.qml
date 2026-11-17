import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

Kirigami.ScrollablePage {
    id: page
    objectName: "appimagePage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
    signal containerRequested()
    signal directRequested()
    // With missing dependencies this page first asks how to install. "Only the
    // application" then opens a second copy of the page with classic set: the usual
    // AppImage review, with the application card and the Install button.
    property bool classic: false
    readonly property bool choosing: welcome.appImageNeedsDependencies && !classic
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, actions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + actionBar.implicitHeight
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
        PackageSummary { Layout.fillWidth: true }
        Kirigami.Heading {
            text: page.choosing
                ? qsTr("Installation method")
                : qsTr("Ready to install")
            level: 2
            Layout.fillWidth: true
        }
        Kirigami.AbstractCard {
            objectName: "appimageApplicationSummary"
            visible: !page.choosing
            Layout.fillWidth: true
            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing
                Image {
                    source: welcome.applicationIcon
                    sourceSize.width: Kirigami.Units.iconSizes.large
                    sourceSize.height: Kirigami.Units.iconSizes.large
                    fillMode: Image.PreserveAspectFit
                    Layout.preferredWidth: Kirigami.Units.iconSizes.large
                    Layout.preferredHeight: Kirigami.Units.iconSizes.large
                }
                Controls.Label {
                    text: welcome.applicationNameHtml
                    textFormat: Text.RichText
                    font.bold: true
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }
        Controls.Label {
            visible: !page.choosing
            text: qsTr("A copy of the AppImage will be saved and a shortcut added to your application launcher.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // Shown only when the analysis found required components missing on the system.
        ColumnLayout {
            visible: page.choosing
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true

            Kirigami.InlineMessage {
                objectName: "appimageDependenciesLink"
                visible: page.choosing
                type: Kirigami.MessageType.Warning
                icon.source: BreezeIcons.source("dialog-warning", Kirigami.Theme.backgroundColor)
                // Only translated, fixed text here: dependency names go to a plain-text window.
                text: qsTr("This AppImage does not include all the dependencies it needs.")
                    + " <a href='dependencies'>" + qsTr("See which") + "</a>"
                activeFocusOnTab: true
                Accessible.role: Accessible.Link
                Accessible.name: qsTr("See which dependencies are missing")
                Accessible.onPressAction: dependencyWindow.open()
                onLinkActivated: dependencyWindow.open()
                Keys.onReturnPressed: dependencyWindow.open()
                Keys.onSpacePressed: dependencyWindow.open()
                Layout.fillWidth: true
            }

            MethodCard {
                objectName: "appimageContainerOption"
                choiceObjectName: "appimageContainerChoice"
                heading: qsTr("Install the application and its dependencies (recommended)")
                description: qsTr("The dependencies will be installed in a container. The system will not be changed.")
                checked: welcome.appImageContainerSelected
                Layout.fillWidth: true
                onSelectionRequested: welcome.chooseAppImageContainer(true)
            }

            MethodCard {
                objectName: "appimageDirectOption"
                choiceObjectName: "appimageDirectChoice"
                heading: qsTr("Install only the application")
                description: qsTr("Any missing dependencies will need to be installed manually later.")
                checked: !welcome.appImageContainerSelected
                Layout.fillWidth: true
                onSelectionRequested: welcome.chooseAppImageContainer(false)
            }
        }
    }

    AppImageDependenciesDialog {
        id: dependencyWindow
        transientParent: page.Window.window
        introduction: qsTr("Not found on your system:")
        dependencies: welcome.appImageDependencyText
    }
    footer: Controls.ToolBar {
        id: actionBar
        padding: page.outerMargin
        contentItem: RowLayout {
            id: actions
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "appimageBackButton"
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }
            Controls.Button {
                objectName: "appimageCancelButton"
                text: qsTr("Cancel")
                onClicked: page.cancelRequested()
            }
            Controls.Button {
                objectName: "appimageInstallButton"
                // While choosing, both options continue to their own review page.
                text: page.choosing ? qsTr("Continue") : qsTr("Install")
                highlighted: true
                onClicked: {
                    if (!page.choosing)
                        welcome.requestInstallation();
                    else if (welcome.appImageContainerSelected)
                        page.containerRequested();
                    else
                        page.directRequested();
                }
            }
        }
    }
}
