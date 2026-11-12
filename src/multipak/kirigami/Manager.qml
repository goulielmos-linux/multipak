import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

Kirigami.ApplicationWindow {
    id: root
    objectName: "managerWindow"
    title: qsTr("Application Manager — MultiPak")
    visible: true
    flags: Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint
           | (manager.canClose ? Qt.WindowCloseButtonHint : 0) | Qt.WindowMinimizeButtonHint
    readonly property var currentPage: legacyPage.item || pageStack.currentItem
    readonly property int fittedWidth: Math.ceil(Math.min(700, Screen.desktopAvailableWidth,
        currentPage ? currentPage.preferredWindowWidth : Kirigami.Units.gridUnit * 31))
    readonly property int fittedHeight: Math.ceil(Math.min(
        Screen.desktopAvailableHeight - Kirigami.Units.gridUnit * 2,
        currentPage ? currentPage.preferredWindowHeight : Kirigami.Units.gridUnit * 22))
    width: fittedWidth
    height: fittedHeight
    minimumWidth: fittedWidth
    maximumWidth: fittedWidth
    minimumHeight: fittedHeight
    maximumHeight: fittedHeight

    function restoreFittedSize() {
        if (!visible || !currentPage)
            return;
        // A native configure event can still carry the previous operation's size.
        if (width !== fittedWidth)
            width = Qt.binding(() => root.fittedWidth);
        if (height !== fittedHeight)
            height = Qt.binding(() => root.fittedHeight);
    }

    onFittedWidthChanged: Qt.callLater(restoreFittedSize)
    onFittedHeightChanged: Qt.callLater(restoreFittedSize)
    onWidthChanged: if (width !== fittedWidth) Qt.callLater(restoreFittedSize)
    onHeightChanged: if (height !== fittedHeight) Qt.callLater(restoreFittedSize)

    onVisibilityChanged: if (visibility === Window.Maximized) showNormal()
    onClosing: close => {
        if (!manager.canClose) {
            close.accepted = false;
            manager.requestClose();
        }
    }

    Connections {
        target: manager
        function onConfirmationRequested() { removalDialog.open(); }
        function onContainerRemovalRequested() { containerRemovalDialog.open(); }
        function onCompanionWarning(message) {
            companionWarningText.text = message;
            companionWarningDialog.open();
        }
        function onLanguageSettingsRequested() {
            languages.reset();
            if (currentPage.objectName !== "languagePage") pageStack.push(languagePage);
        }
        function onAssociationSettingsRequested() {
            languages.reset();
            if (currentPage.objectName !== "managerSettingsPage") pageStack.push(associationPage);
        }
    }

    Component {
        id: associationPage
        AssociationPage {
            onAboutRequested: aboutDialog.open()
            onReturnRequested: {
                manager.goHome();
                root.pageStack.pop();
            }
        }
    }

    Component {
        id: languagePage
        LanguagePage {
            onAboutRequested: aboutDialog.open()
            onReturnRequested: {
                manager.goHome();
                root.pageStack.pop();
            }
        }
    }

    pageStack.defaultColumnWidth: pageStack.width
    pageStack.interactive: false
    pageStack.columnView.scrollDuration: 0
    pageStack.columnView.clip: true
    pageStack.enabled: !appUpdates.checking && !legacyMigration.checking && !legacyMigration.required
    pageStack.visible: !legacyMigration.required
    Loader {
        id: legacyPage
        parent: root.contentItem
        anchors.fill: parent
        active: legacyMigration.required
        sourceComponent: LegacyMigrationPage {
            onCloseRequested: root.close()
        }
    }
    UpdateNotice {
        transientParent: root
    }

    pageStack.globalToolBar.style: Kirigami.ApplicationHeaderStyle.None
    pageStack.initialPage: Component {
        ManagerPage {
            onAboutRequested: aboutDialog.open()
            onCloseRequested: root.close()
            onSettingsRequested: manager.openAssociations()
        }
    }

    InformationWindow {
        id: removalDialog
        objectName: "removalDialog"
        transientParent: root
        title: qsTr("Uninstall application")
        onClosing: manager.cancelRemoval()
        onShown: cancelRemovalButton.forceActiveFocus()
        Controls.Label {
            objectName: "removalConfirmationText"
            text: qsTr("Uninstall %1?\n\n%2").arg(manager.confirmationName).arg(manager.removalExplanation)
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: cancelRemovalButton
                    objectName: "cancelRemovalButton"
                    text: qsTr("Cancel")
                    onClicked: removalDialog.close()
                }
                Controls.Button {
                    objectName: "confirmRemovalButton"
                    text: qsTr("Uninstall")
                    onClicked: {
                        manager.confirmRemoval();
                        removalDialog.close();
                    }
                }
            }
        }
    }

    InformationWindow {
        id: containerRemovalDialog
        objectName: "containerRemovalDialog"
        transientParent: root
        title: qsTr("Remove container?")
        color: Kirigami.Theme.backgroundColor
        onClosing: manager.keepContainer()
        onShown: keepContainerButton.forceActiveFocus()
        Controls.Label {
            objectName: "containerRemovalText"
            text: qsTr("Container %1 has no applications registered in the Application Manager. Would you like to remove it to free up disk space?").arg(manager.unusedContainerName)
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        RowLayout {
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true
            Kirigami.Icon {
                objectName: "containerRemovalWarningIcon"
                source: BreezeIcons.source("dialog-warning", Kirigami.Theme.backgroundColor)
                Accessible.ignored: true
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.preferredHeight: Kirigami.Units.iconSizes.large
                Layout.alignment: Qt.AlignTop
            }
            Controls.Label {
                objectName: "containerRemovalWarning"
                text: qsTr("MultiPak may not detect applications installed in this container by other means. Deleting the container will also remove those applications and all remaining packages.")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
        Controls.Label {
            text: manager.canRemoveContainerHome
                ? qsTr("The container's home folder will be kept unless you select the option below. Files in other folders inside the container will be deleted.")
                : qsTr("The container's home folder will be kept. Files in other folders inside the container will be deleted.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Controls.CheckBox {
            id: removeContainerHomeCheckBox
            objectName: "removeContainerHomeCheckBox"
            visible: manager.canRemoveContainerHome
            checked: manager.removeContainerHome
            text: qsTr("Also delete the container's home folder, including its data and settings.")
            onToggled: manager.setRemoveContainerHome(checked)
            Layout.fillWidth: true
            contentItem: Controls.Label {
                text: removeContainerHomeCheckBox.text
                leftPadding: removeContainerHomeCheckBox.indicator.width + removeContainerHomeCheckBox.spacing
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
            }
        }
        Controls.Label {
            objectName: "containerRemovalError"
            text: manager.containerRemovalError
            visible: text.length > 0
            textFormat: Text.PlainText
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: keepContainerButton
                    objectName: "keepContainerButton"
                    text: qsTr("Keep container")
                    highlighted: true
                    onClicked: containerRemovalDialog.close()
                }
                Controls.Button {
                    objectName: "deleteContainerButton"
                    text: qsTr("Delete container")
                    onClicked: {
                        manager.confirmContainerRemoval();
                        containerRemovalDialog.close();
                    }
                }
            }
        }
    }

    InformationWindow {
        id: companionWarningDialog
        objectName: "companionWarningDialog"
        transientParent: root
        title: qsTr("Could not open the application installer.")
        color: Kirigami.Theme.backgroundColor
        onShown: companionWarningOk.forceActiveFocus()
        Controls.Label {
            id: companionWarningText
            objectName: "companionWarningText"
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: companionWarningOk
                    objectName: "companionWarningOk"
                    text: "OK"
                    highlighted: true
                    onClicked: companionWarningDialog.close()
                }
            }
        }
    }

    AboutWindow {
        id: aboutDialog
        objectName: "managerAboutDialog"
        transientParent: root
        title: qsTr("About the Application Manager — MultiPak")
        applicationTitle: qsTr("MultiPak Application Manager")
        applicationDescription: qsTr("Manage your applications and set your preferences.")
        iconSource: "assets/multipak-manager.svg"
        titleObjectName: "managerAboutText"
        licenseObjectName: "managerLicenseButton"
        licenseDialogObjectName: "managerLicenseDialog"
    }
}
