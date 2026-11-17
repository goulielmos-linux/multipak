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
    readonly property int fittedWidth: Math.ceil(Math.min(1100, Screen.desktopAvailableWidth,
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
        } else {
            manager.android.stopStats();
        }
    }

    Connections {
        target: manager
        function onConfirmationRequested() { removalDialog.open(); }
        function onRepairConfirmationRequested() { repairDialog.open(); }
        function onDeleteConfirmationRequested() { deleteDialog.open(); }
        function onUpdateLinkRequested() { updateLinkDialog.open(); }
        function onUpdateClosingRequested() { updateClosingDialog.open(); }
        function onUpdateDependenciesRequested() { updateDependenciesDialog.open(); }
        function onGroupUpdateRequested() { groupUpdateDialog.open(); }
        function onSnapPermissionsRequested(message) {
            snapPermissionsDialog.message = message;
            snapPermissionsDialog.open();
        }
        function onContainerRemovalRequested() { containerRemovalDialog.open(); }
        function onCompanionWarning(message) {
            companionWarningText.text = message;
            companionWarningDialog.open();
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
        ManagerShell {
            onAboutRequested: aboutDialog.open()
            onCloseRequested: root.close()
            onAdminCommandRequested: arguments => {
                adminDialog.arguments = arguments;
                adminDialog.command = manager.adminCommandName;
                adminDialog.open();
            }
        }
    }

    InformationWindow {
        id: adminDialog
        objectName: "adminDialog"
        property string arguments: ""
        property string command: ""
        transientParent: root
        title: qsTr("Run as administrator?")
        onShown: cancelAdminButton.forceActiveFocus()
        Controls.Label {
            objectName: "adminCommandText"
            text: (adminDialog.command + " " + adminDialog.arguments).trim()
            font.family: "monospace"
            textFormat: Text.PlainText
            wrapMode: Text.WrapAnywhere
            Layout.fillWidth: true
        }
        RowLayout {
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true
            Kirigami.Icon {
                source: BreezeIcons.source("dialog-warning", Kirigami.Theme.backgroundColor)
                Accessible.ignored: true
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.preferredHeight: Kirigami.Units.iconSizes.large
                Layout.alignment: Qt.AlignTop
            }
            Controls.Label {
                objectName: "adminWarning"
                text: qsTr("The command runs as root inside the snap's sandbox. It can read, change and delete all of this snap's data, and run any program of the snap. It does not get administrator rights on your computer. Run only commands you trust, such as those in the snap's documentation.")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: cancelAdminButton
                    objectName: "cancelAdminButton"
                    text: qsTr("Cancel")
                    onClicked: adminDialog.close()
                }
                Controls.Button {
                    objectName: "confirmAdminButton"
                    text: qsTr("Run as administrator")
                    onClicked: {
                        adminDialog.close();
                        manager.runAdminCommand(adminDialog.arguments);
                    }
                }
            }
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
        id: repairDialog
        objectName: "repairDialog"
        transientParent: root
        title: qsTr("Repair application")
        onClosing: manager.cancelRepair()
        onShown: cancelRepairButton.forceActiveFocus()
        Controls.Label {
            objectName: "repairConfirmationText"
            text: qsTr("Repair %1?\n\n%2").arg(manager.repairName).arg(manager.repairExplanation)
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: cancelRepairButton
                    objectName: "cancelRepairButton"
                    text: qsTr("Cancel")
                    onClicked: repairDialog.close()
                }
                Controls.Button {
                    objectName: "confirmRepairButton"
                    text: qsTr("Confirm")
                    highlighted: true
                    onClicked: {
                        manager.confirmRepair();
                        repairDialog.close();
                    }
                }
            }
        }
    }

    InformationWindow {
        id: deleteDialog
        objectName: "deleteDialog"
        transientParent: root
        title: qsTr("Delete application")
        onClosing: manager.cancelDelete()
        onShown: cancelDeleteButton.forceActiveFocus()
        Controls.Label {
            objectName: "deleteConfirmationText"
            text: qsTr("Delete %1?\n\nMultiPak will delete its copy of the AppImage, along with its shortcut and icon in the application launcher.\n\nTo use the application again, you will need to install the AppImage again.").arg(manager.deleteName)
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: cancelDeleteButton
                    objectName: "cancelDeleteButton"
                    text: qsTr("Cancel")
                    onClicked: deleteDialog.close()
                }
                Controls.Button {
                    objectName: "confirmDeleteButton"
                    text: qsTr("Delete")
                    icon.source: BreezeIcons.source("edit-delete", Kirigami.Theme.backgroundColor)
                    palette.buttonText: Kirigami.Theme.negativeTextColor
                    onClicked: {
                        manager.confirmDelete();
                        deleteDialog.close();
                    }
                }
            }
        }
    }

    // Updating an AppImage. Each window either continues the update or cancels it.
    InformationWindow {
        id: updateLinkDialog
        objectName: "updateLinkDialog"
        property bool proceeding: false
        transientParent: root
        title: qsTr("Update application")
        onShown: {
            proceeding = false;
            // A new update starts empty; after an error the link stays to be corrected.
            if (manager.updateLinkError.length === 0)
                updateLinkField.text = "";
            updateLinkField.forceActiveFocus();
        }
        onClosing: if (!proceeding) manager.cancelAppImageUpdate()
        Controls.Label {
            objectName: "updateLinkText"
            text: qsTr("The application does not indicate its update address. Enter it below:")
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        Controls.TextField {
            id: updateLinkField
            objectName: "updateLinkField"
            placeholderText: "https://"
            Accessible.name: qsTr("Update address")
            Layout.fillWidth: true
            onAccepted: if (text.trim().length > 0) continueLinkButton.clicked()
        }
        Controls.Label {
            objectName: "updateLinkError"
            text: manager.updateLinkError
            visible: text.length > 0
            textFormat: Text.PlainText
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    objectName: "cancelUpdateLinkButton"
                    text: qsTr("Back")
                    onClicked: updateLinkDialog.close()
                }
                Controls.Button {
                    id: continueLinkButton
                    objectName: "continueUpdateLinkButton"
                    text: qsTr("Continue")
                    highlighted: true
                    enabled: updateLinkField.text.trim().length > 0
                    onClicked: {
                        updateLinkDialog.proceeding = true;
                        manager.submitUpdateLink(updateLinkField.text.trim());
                        updateLinkDialog.close();
                    }
                }
            }
        }
    }

    InformationWindow {
        id: updateClosingDialog
        objectName: "updateClosingDialog"
        property bool proceeding: false
        transientParent: root
        title: qsTr("Update application")
        onShown: {
            proceeding = false;
            backClosingButton.forceActiveFocus();
        }
        onClosing: if (!proceeding) manager.cancelUpdateClosing()
        Controls.Label {
            objectName: "updateClosingText"
            text: qsTr("%1 needs to be closed.\n\nIf MultiPak closes it for you, unsaved work in it may be lost.").arg(manager.updateName)
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: backClosingButton
                    objectName: "cancelUpdateClosingButton"
                    text: qsTr("Back")
                    onClicked: updateClosingDialog.close()
                }
                Controls.Button {
                    objectName: "confirmUpdateClosingButton"
                    text: qsTr("Close it for me")
                    onClicked: {
                        updateClosingDialog.proceeding = true;
                        manager.closeUpdatedApplication();
                        updateClosingDialog.close();
                    }
                }
            }
        }
    }

    // Applications sharing a container are updated with it, together.
    InformationWindow {
        id: groupUpdateDialog
        objectName: "groupUpdateDialog"
        property bool proceeding: false
        transientParent: root
        title: qsTr("Update %1?").arg(manager.groupUpdateName)
        onShown: {
            proceeding = false;
            cancelGroupUpdateButton.forceActiveFocus();
        }
        onClosing: if (!proceeding) manager.cancelGroupUpdate()
        Controls.Label {
            objectName: "groupUpdateText"
            text: qsTr("%1 is installed in a container, so MultiPak updates the whole container, including these applications:\n\n%2\n\nThe container's dependencies and system components are also updated. Your system outside the container is not changed.").arg(manager.groupUpdateName).arg(manager.groupUpdateOthers)
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: cancelGroupUpdateButton
                    objectName: "cancelGroupUpdateButton"
                    text: qsTr("Cancel")
                    onClicked: groupUpdateDialog.close()
                }
                Controls.Button {
                    objectName: "confirmGroupUpdateButton"
                    text: qsTr("Update all of them")
                    highlighted: true
                    onClicked: {
                        groupUpdateDialog.proceeding = true;
                        manager.confirmGroupUpdate();
                        groupUpdateDialog.close();
                    }
                }
            }
        }
    }

    InformationWindow {
        id: snapPermissionsDialog
        objectName: "snapPermissionsDialog"
        property string message: ""
        property bool proceeding: false
        transientParent: root
        title: qsTr("Snap update permissions")
        onShown: {
            proceeding = false;
            cancelSnapPermissionsButton.forceActiveFocus();
        }
        onClosing: if (!proceeding) manager.answerSnapPermissions(false)
        Controls.ScrollView {
            contentWidth: availableWidth
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(newSnapPermissions.implicitHeight,
                                            Kirigami.Units.gridUnit * 14)
            Controls.Label {
                id: newSnapPermissions
                width: parent.width
                text: snapPermissionsDialog.message
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: cancelSnapPermissionsButton
                    text: qsTr("Cancel")
                    onClicked: snapPermissionsDialog.close()
                }
                Controls.Button {
                    text: qsTr("Update")
                    highlighted: true
                    onClicked: {
                        snapPermissionsDialog.proceeding = true;
                        manager.answerSnapPermissions(true);
                        snapPermissionsDialog.close();
                    }
                }
            }
        }
    }

    InformationWindow {
        id: updateDependenciesDialog
        objectName: "updateDependenciesDialog"
        property bool proceeding: false
        transientParent: root
        title: qsTr("Update application")
        onShown: {
            proceeding = false;
            backDependenciesButton.forceActiveFocus();
        }
        onClosing: if (!proceeding) manager.cancelAppImageUpdate()
        RowLayout {
            spacing: Kirigami.Units.smallSpacing
            Layout.fillWidth: true
            Controls.Label {
                objectName: "updateDependenciesText"
                text: qsTr("The new version requires new dependencies.")
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            Controls.Label {
                objectName: "updateDependenciesLink"
                text: qsTr("<a href='dependencies'>View full list</a>")
                textFormat: Text.RichText
                linkColor: Kirigami.Theme.linkColor
                Accessible.role: Accessible.Link
                Accessible.name: qsTr("View the full list of required dependencies")
                activeFocusOnTab: true
                onLinkActivated: updateDependencyList.open()
                Keys.onReturnPressed: updateDependencyList.open()
                Keys.onSpacePressed: updateDependencyList.open()
                HoverHandler {
                    cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor
                }
            }
            Item { Layout.fillWidth: true }
        }
        footer: Controls.ToolBar {
            padding: Kirigami.Units.largeSpacing * 2
            contentItem: RowLayout {
                Item { Layout.fillWidth: true }
                Controls.Button {
                    id: backDependenciesButton
                    objectName: "cancelUpdateDependenciesButton"
                    text: qsTr("Back")
                    onClicked: updateDependenciesDialog.close()
                }
                Controls.Button {
                    objectName: "confirmUpdateDependenciesButton"
                    text: qsTr("Install")
                    highlighted: true
                    onClicked: {
                        updateDependenciesDialog.proceeding = true;
                        manager.confirmAppImageUpdate();
                        updateDependenciesDialog.close();
                    }
                }
            }
        }
    }

    AppImageDependenciesDialog {
        id: updateDependencyList
        objectName: "updateDependencyList"
        transientParent: updateDependenciesDialog
        title: qsTr("Required dependencies")
        dependencies: manager.updateDependencies
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
            text: manager.unusedContainerIsAndroid
                ? qsTr("The last user-installed application was removed from %1. Would you like to remove this Android container to free up disk space?").arg(manager.unusedContainerName)
                : qsTr("Container %1 has no applications registered in the Application Manager. Would you like to remove it to free up disk space?").arg(manager.unusedContainerName)
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        RowLayout {
            visible: !manager.unusedContainerIsAndroid
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
            text: manager.unusedContainerIsAndroid
                ? qsTr("Saved Android application data will be preserved unless you select the option below.")
                : manager.canRemoveContainerHome
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
            text: manager.unusedContainerIsAndroid
                ? qsTr("Also delete this environment's Android data")
                : qsTr("Also delete the container's home folder, including its data and settings.")
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
