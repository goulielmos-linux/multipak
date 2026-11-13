import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "appimagePage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
    signal containerRequested()
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
            text: welcome.appImageNeedsDependencies
                ? qsTr("Missing dependencies")
                : qsTr("Ready to install")
            level: 2
            Layout.fillWidth: true
        }
        Kirigami.AbstractCard {
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
            visible: !welcome.appImageNeedsDependencies
            text: qsTr("A copy of the AppImage will be saved and a shortcut added to your application launcher.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // Shown only when the analysis found required components missing on the system.
        ColumnLayout {
            visible: welcome.appImageNeedsDependencies
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true

            Controls.Label {
                objectName: "appimageDependenciesLink"
                // Only translated, fixed text here: dependency names go to a plain-text window.
                text: qsTr("This AppImage does not include all the dependencies it needs.")
                    + " <a href='dependencies'>" + qsTr("See which") + "</a>."
                textFormat: Text.RichText
                linkColor: Kirigami.Theme.linkColor
                wrapMode: Text.WordWrap
                activeFocusOnTab: true
                Accessible.role: Accessible.Link
                Accessible.name: qsTr("See which dependencies are missing")
                onLinkActivated: dependencyWindow.open()
                Keys.onReturnPressed: dependencyWindow.open()
                Keys.onSpacePressed: dependencyWindow.open()
                Layout.fillWidth: true
                HoverHandler {
                    cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor
                }
            }

            MethodCard {
                objectName: "appimageContainerOption"
                choiceObjectName: "appimageContainerChoice"
                heading: qsTr("Install the application and its dependencies in a container (recommended)")
                description: qsTr("The system will not be changed.")
                checked: welcome.appImageContainerSelected
                Layout.fillWidth: true
                onSelectionRequested: welcome.chooseAppImageContainer(true)
            }

            MethodCard {
                objectName: "appimageDirectOption"
                choiceObjectName: "appimageDirectChoice"
                heading: qsTr("Install anyway")
                description: qsTr("The application will be installed, but you will need to install the missing dependencies later.")
                checked: !welcome.appImageContainerSelected
                Layout.fillWidth: true
                onSelectionRequested: welcome.chooseAppImageContainer(false)
            }
        }
    }

    AppImageDependenciesDialog {
        id: dependencyWindow
        transientParent: page.Window.window
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
                // The container path continues to a page that shows where it will go.
                text: welcome.appImageContainerSelected ? qsTr("Continue") : qsTr("Install")
                highlighted: true
                onClicked: {
                    if (welcome.appImageContainerSelected)
                        page.containerRequested();
                    else
                        welcome.requestInstallation();
                }
            }
        }
    }
}
