import QtCore
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import QtQuick.Dialogs as Dialogs
import org.kde.kirigami as Kirigami
import "BreezeIcons.js" as BreezeIcons

Kirigami.ScrollablePage {
    id: page
    objectName: "androidEnvironmentsPage"
    signal returnRequested()
    signal aboutRequested()
    // Inside the Application Manager's side bar: no header and no Back button.
    property bool embedded: false
    readonly property var controller: manager.android
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Kirigami.Units.gridUnit * 35
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + footerBar.implicitHeight
    property string removalId: ""
    property string removalName: ""
    property string updateId: ""
    property string updateName: ""
    property string sendId: ""
    property string sendName: ""
    property string microphoneId: ""
    property string microphoneName: ""
    property bool detailsExpanded: false
    padding: outerMargin

    Timer {
        interval: 5000
        running: page.visible && !controller.busy
        repeat: true
        triggeredOnStart: true
        onTriggered: controller.refreshStats()
    }

    ColumnLayout {
        id: body
        width: page.availableWidth
        spacing: Kirigami.Units.largeSpacing * 2
        BrandHeader {
            visible: !page.embedded
            heading: qsTr("MultiPak Application Manager")
            logoSource: "assets/multipak-manager.svg"
            showAbout: true
            Layout.fillWidth: true
            onAboutRequested: page.aboutRequested()
        }
        RowLayout {
            Layout.fillWidth: true
            Kirigami.Heading {
                text: page.embedded ? qsTr("Waydroid integration") : qsTr("Android containers")
                level: 2
                Layout.fillWidth: true
            }
            Controls.ToolButton {
                objectName: "androidManagerRefresh"
                icon.source: BreezeIcons.source("view-refresh", Kirigami.Theme.backgroundColor)
                text: qsTr("Refresh")
                display: Controls.AbstractButton.IconOnly
                enabled: !controller.busy
                onClicked: controller.refresh()
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: text
            }
            Controls.Button {
                objectName: "androidManagerNew"
                text: qsTr("Create container")
                icon.source: BreezeIcons.source("list-add", Kirigami.Theme.backgroundColor)
                highlighted: true
                enabled: !controller.busy
                onClicked: createDialog.open()
            }
        }
        Controls.CheckBox {
            objectName: "androidManagerAskLinks"
            text: qsTr("Ask which app to use when a link can open in Android or on Linux")
            checked: controller.askLinks
            enabled: !controller.busy
            onToggled: {
                controller.setAskLinks(checked);
                checked = Qt.binding(() => controller.askLinks);
            }
            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: qsTr("Only for addresses that another app or program also opens.")
            Layout.fillWidth: true
        }
        Kirigami.AbstractCard {
            visible: !controller.busy && controller.rows.length === 0
            Layout.fillWidth: true
            contentItem: ColumnLayout {
                spacing: Kirigami.Units.largeSpacing
                Kirigami.Icon {
                    source: Qt.resolvedUrl("assets/container.svg")
                    Layout.preferredWidth: Kirigami.Units.iconSizes.huge
                    Layout.preferredHeight: Kirigami.Units.iconSizes.huge
                    Layout.alignment: Qt.AlignHCenter
                    Accessible.ignored: true
                }
                Controls.Label {
                    text: qsTr("No Android containers yet")
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
                Controls.Label {
                    text: qsTr("Create a container to keep your Android applications together.")
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
            }
        }
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            Layout.fillWidth: true
            Repeater {
                model: controller.rows
                Kirigami.AbstractCard {
                    id: containerCard
                    required property int index
                    required property var modelData
                    objectName: "androidEnvironmentRow" + index
                    readonly property bool active: modelData.can_stop === true
                    Layout.fillWidth: true
                    contentItem: RowLayout {
                        spacing: Kirigami.Units.largeSpacing
                        Kirigami.Icon {
                            source: Qt.resolvedUrl("assets/container.svg")
                            Layout.preferredWidth: Kirigami.Units.iconSizes.large
                            Layout.preferredHeight: Kirigami.Units.iconSizes.large
                            Accessible.ignored: true
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Kirigami.Units.smallSpacing
                            Controls.Label {
                                text: containerCard.modelData.label
                                textFormat: Text.PlainText
                                font.bold: true
                                wrapMode: Text.WrapAnywhere
                                Layout.fillWidth: true
                            }
                            RowLayout {
                                Controls.Label {
                                    text: containerCard.modelData.version
                                    color: Kirigami.Theme.disabledTextColor
                                }
                                Rectangle {
                                    id: badge
                                    readonly property color statusColor: containerCard.modelData.state === "running"
                                        ? Kirigami.Theme.positiveTextColor
                                        : containerCard.modelData.state === "frozen"
                                            ? Kirigami.Theme.neutralTextColor
                                            : Kirigami.Theme.disabledTextColor
                                    color: Qt.rgba(statusColor.r, statusColor.g, statusColor.b, 0.12)
                                    radius: height / 2
                                    implicitWidth: stateText.implicitWidth + Kirigami.Units.largeSpacing * 2
                                    implicitHeight: stateText.implicitHeight + Kirigami.Units.smallSpacing * 2
                                    Controls.Label {
                                        id: stateText
                                        anchors.centerIn: parent
                                        text: containerCard.modelData.state_label || qsTr("Status unavailable")
                                        color: badge.statusColor
                                    }
                                    HoverHandler { id: badgeHover }
                                    Controls.ToolTip.visible: badgeHover.hovered && (containerCard.modelData.state_explanation || "").length > 0
                                    Controls.ToolTip.text: containerCard.modelData.state_explanation || ""
                                }
                            }
                            Controls.Label {
                                objectName: "androidManagerStats" + containerCard.index
                                readonly property var counters: controller.stats[containerCard.modelData.id]
                                text: qsTr("CPU: %1    RAM: %2")
                                    .arg(counters ? counters.cpu : "—")
                                    .arg(counters ? counters.memory : "—")
                                font: Kirigami.Theme.smallFont
                                color: Kirigami.Theme.disabledTextColor
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                        Controls.ToolButton {
                            objectName: "androidManagerPower" + containerCard.index
                            icon.source: BreezeIcons.source(containerCard.active ? "process-stop-symbolic" : "media-playback-start", Kirigami.Theme.backgroundColor)
                            text: containerCard.active ? qsTr("Stop") : qsTr("Start")
                            display: Controls.AbstractButton.IconOnly
                            enabled: !controller.busy
                            onClicked: controller.actOn(containerCard.modelData.id, containerCard.active ? "stop" : "start")
                            Controls.ToolTip.visible: hovered
                            Controls.ToolTip.text: text
                        }
                        Controls.Button {
                            objectName: "androidManagerOpen" + containerCard.index
                            text: qsTr("Open Android")
                            enabled: !controller.busy
                            onClicked: controller.actOn(containerCard.modelData.id, "open")
                        }
                        Controls.ToolButton {
                            objectName: "androidManagerMore" + containerCard.index
                            icon.source: BreezeIcons.source("view-more-symbolic", Kirigami.Theme.backgroundColor)
                            text: qsTr("More options")
                            display: Controls.AbstractButton.IconOnly
                            enabled: !controller.busy
                            onClicked: containerMenu.popup()
                            Controls.ToolTip.visible: hovered
                            Controls.ToolTip.text: text
                            Controls.Menu {
                                id: containerMenu
                                objectName: "androidManagerMenu" + containerCard.index
                                Controls.MenuItem {
                                    objectName: "androidManagerUpdate" + containerCard.index
                                    text: qsTr("Update container")
                                    icon.source: BreezeIcons.source("system-software-update", Kirigami.Theme.backgroundColor)
                                    enabled: !controller.busy && containerCard.modelData.initialized === true
                                    onTriggered: {
                                        page.updateId = containerCard.modelData.id;
                                        page.updateName = containerCard.modelData.label;
                                        updateDialog.open();
                                    }
                                }
                                Controls.MenuItem {
                                    objectName: "androidManagerRestart" + containerCard.index
                                    text: qsTr("Restart container")
                                    icon.source: BreezeIcons.source("view-refresh", Kirigami.Theme.backgroundColor)
                                    enabled: !controller.busy && containerCard.active
                                    onTriggered: controller.actOn(containerCard.modelData.id, "restart")
                                }
                                Controls.MenuItem {
                                    objectName: "androidManagerKeepReady" + containerCard.index
                                    text: qsTr("Start at login")
                                    icon.source: BreezeIcons.source("user-online", Kirigami.Theme.backgroundColor)
                                    checkable: true
                                    checked: containerCard.modelData.keep_ready === true
                                    enabled: !controller.busy && containerCard.modelData.initialized === true
                                    onTriggered: controller.setKeepReady(containerCard.modelData.id, checked)
                                }
                                Controls.MenuItem {
                                    objectName: "androidManagerKeyboard" + containerCard.index
                                    text: qsTr("Show the virtual keyboard")
                                    icon.source: BreezeIcons.source("input-keyboard-symbolic", Kirigami.Theme.backgroundColor)
                                    checkable: true
                                    checked: containerCard.modelData.keyboard === true
                                    enabled: !controller.busy && containerCard.modelData.initialized === true
                                    onTriggered: controller.setKeyboard(containerCard.modelData.id, checked)
                                }
                                Controls.MenuItem {
                                    objectName: "androidManagerSend" + containerCard.index
                                    text: qsTr("Send files")
                                    icon.source: BreezeIcons.source("document-send", Kirigami.Theme.backgroundColor)
                                    enabled: !controller.busy && !controller.sending.busy && containerCard.modelData.initialized === true
                                    onTriggered: {
                                        page.sendId = containerCard.modelData.id;
                                        page.sendName = containerCard.modelData.label;
                                        filePicker.open();
                                    }
                                }
                                Controls.MenuItem {
                                    objectName: "androidManagerMicrophone" + containerCard.index
                                    text: qsTr("Camera and microphone")
                                    icon.source: BreezeIcons.source("camera-web", Kirigami.Theme.backgroundColor)
                                    enabled: !controller.busy && containerCard.modelData.initialized === true
                                    onTriggered: {
                                        page.microphoneId = containerCard.modelData.id;
                                        page.microphoneName = containerCard.modelData.label;
                                        controller.loadCamera(page.microphoneId);
                                        controller.loadMicrophone(page.microphoneId);
                                        microphoneDialog.open();
                                    }
                                }
                                Controls.MenuItem {
                                    objectName: "androidManagerRemove" + containerCard.index
                                    text: qsTr("Remove container")
                                    icon.source: BreezeIcons.source("edit-delete", Kirigami.Theme.backgroundColor)
                                    enabled: !controller.busy
                                    onTriggered: {
                                        page.removalId = containerCard.modelData.id;
                                        page.removalName = containerCard.modelData.label;
                                        removeDialog.open();
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        ColumnLayout {
            visible: controller.busy || controller.status.length > 0 || controller.logs.length > 0
            Layout.fillWidth: true
            spacing: Kirigami.Units.largeSpacing * 2
            RowLayout {
                visible: controller.busy || controller.status.length > 0
                Layout.fillWidth: true
                Controls.BusyIndicator {
                    visible: controller.busy
                    running: visible
                    Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                    Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                }
                Controls.Label {
                    objectName: "androidManagerStatus"
                    text: controller.status
                    color: controller.hasError ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Controls.Button {
                    text: qsTr("Cancel")
                    visible: controller.canCancel
                    onClicked: controller.cancel()
                }
            }
            Controls.ProgressBar {
                visible: controller.busy
                from: 0; to: 100
                value: Math.max(0, controller.progress)
                indeterminate: controller.progress < 0
                Layout.fillWidth: true
            }
            Controls.ToolButton {
                objectName: "androidManagerDetailsButton"
                text: page.detailsExpanded ? qsTr("Hide details") : qsTr("Show details")
                visible: controller.logs.length > 0
                onClicked: page.detailsExpanded = !page.detailsExpanded
            }
            LogView {
                objectName: "androidManagerLogView"
                text: page.detailsExpanded ? controller.logs : ""
                visible: page.detailsExpanded && controller.logs.length > 0
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.gridUnit * 9
            }
        }
    }

    Controls.Dialog {
        id: updateDialog
        objectName: "androidUpdateDialog"
        parent: Controls.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(page.width - page.outerMargin * 2, Kirigami.Units.gridUnit * 29)
        title: qsTr("Update container")
        modal: true
        standardButtons: Controls.Dialog.Cancel | Controls.Dialog.Ok
        contentItem: Controls.Label {
            text: qsTr("The Android system in %1 will be updated. Its applications and saved data will be preserved. This does not update the applications or change the Android version. Open applications may close during the update. System authorization may be required.").arg(page.updateName)
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
        }
        onAccepted: controller.updateInstance(page.updateId)
    }
    Controls.Dialog {
        id: createDialog
        objectName: "androidCreateDialog"
        parent: Controls.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(page.width - page.outerMargin * 2, Kirigami.Units.gridUnit * 29)
        title: qsTr("Create Android container")
        modal: true
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            Controls.Label { text: qsTr("Android version"); Layout.fillWidth: true }
            Controls.ComboBox {
                id: profile
                objectName: "androidManagerProfile"
                model: controller.profiles
                textRole: "label"
                valueRole: "id"
                Layout.fillWidth: true
            }
            Controls.Label { text: qsTr("Container name"); Layout.fillWidth: true }
            Controls.TextField {
                id: environmentName
                objectName: "androidManagerName"
                maximumLength: 64
                validator: RegularExpressionValidator { regularExpression: /[^\x00-\x1f\x7f]{0,64}/ }
                placeholderText: qsTr("Container name")
                Layout.fillWidth: true
            }
            Controls.Label {
                text: qsTr("Up to 64 characters. Spaces and accents are allowed.")
                color: Kirigami.Theme.disabledTextColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Controls.Label {
                text: qsTr("The first preparation downloads the selected Android version and may request your system password.")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
        footer: Controls.DialogButtonBox {
            Controls.Button { text: qsTr("Cancel"); Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.RejectRole }
            Controls.Button {
                objectName: "androidManagerCreate"
                text: qsTr("Create")
                highlighted: true
                enabled: !controller.busy && profile.currentIndex >= 0
                    && environmentName.text.trim().length > 0
                    && !/[\x00-\x1f\x7f]/.test(environmentName.text)
                Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.AcceptRole
            }
        }
        onOpened: {
            profile.currentIndex = 0;
            environmentName.text = "";
            environmentName.forceActiveFocus();
        }
        onAccepted: controller.create(profile.currentValue, environmentName.text.trim())
    }
    Controls.Dialog {
        // Waydroid is missing or too old: what MultiPak can do, as in the Installation Wizard.
        id: waydroidSetupDialog
        objectName: "androidWaydroidSetupDialog"
        readonly property var setup: controller.waydroidSetup
        parent: Controls.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(page.width - page.outerMargin * 2, Kirigami.Units.gridUnit * 29)
        modal: true
        title: setup.restartRequired ? qsTr("Restart required")
            : setup.updateRequired ? qsTr("Update required")
            : setup.canInstall ? qsTr("Install %1").arg("Waydroid") : "Waydroid"
        contentItem: Controls.Label {
            text: waydroidSetupDialog.setup.message || ""
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
        }
        footer: Controls.DialogButtonBox {
            Controls.Button {
                objectName: "androidWaydroidSetupHelp"
                text: qsTr("See how")
                icon.name: "help-contents"
                visible: !!waydroidSetupDialog.setup.helpUrl
                Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.HelpRole
                onClicked: Qt.openUrlExternally(waydroidSetupDialog.setup.helpUrl)
            }
            Controls.Button {
                text: waydroidSetupDialog.setup.restartRequired ? qsTr("Restart later")
                    : waydroidSetupDialog.setup.canInstall ? qsTr("Cancel") : qsTr("Close")
                Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.RejectRole
            }
            Controls.Button {
                objectName: "androidWaydroidSetupInstall"
                text: qsTr("Install %1").arg("Waydroid")
                highlighted: true
                visible: !!waydroidSetupDialog.setup.canInstall
                Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.AcceptRole
            }
            Controls.Button {
                objectName: "androidWaydroidSetupRestart"
                text: qsTr("Restart now")
                highlighted: true
                visible: !!waydroidSetupDialog.setup.restartRequired
                Controls.DialogButtonBox.buttonRole: Controls.DialogButtonBox.AcceptRole
            }
        }
        onAccepted: {
            if (setup.canInstall)
                controller.installWaydroid();
            else if (setup.restartRequired)
                controller.restartForWaydroid();
        }
        onRejected: controller.dismissWaydroidSetup()
    }
    Connections {
        // Open the Waydroid setup whenever the controller has one, and close it when it is done.
        target: controller
        function onChanged() {
            if (controller.waydroidSetup.message !== undefined) {
                if (!waydroidSetupDialog.opened)
                    waydroidSetupDialog.open();
            } else if (waydroidSetupDialog.opened) {
                waydroidSetupDialog.close();
            }
        }
    }
    Controls.Dialog {
        id: removeDialog
        objectName: "androidRemoveDialog"
        parent: Controls.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(page.width - page.outerMargin * 2, Kirigami.Units.gridUnit * 29)
        title: qsTr("Remove Android container?")
        modal: true
        standardButtons: Controls.Dialog.Cancel | Controls.Dialog.Ok
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            Controls.Label {
                text: qsTr("Remove %1? All applications in this environment will become unavailable. Other Android environments will be preserved.").arg(page.removalName)
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Controls.CheckBox {
                id: deleteData
                objectName: "androidDeleteData"
                text: qsTr("Also delete this environment's Android data")
                Layout.fillWidth: true
                contentItem: Controls.Label {
                    text: deleteData.text
                    leftPadding: deleteData.indicator.width + deleteData.spacing
                    wrapMode: Text.WordWrap
                }
            }
        }
        onOpened: deleteData.checked = false
        onAccepted: controller.removeInstance(page.removalId, deleteData.checked)
    }
    Controls.Dialog {
        id: sendDialog
        objectName: "androidSendDialog"
        parent: Controls.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(page.width - page.outerMargin * 2, Kirigami.Units.gridUnit * 24)
        title: qsTr("Sending to %1").arg(page.sendName)
        modal: true
        closePolicy: Controls.Popup.NoAutoClose
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            Controls.Label {
                text: controller.sending.error || controller.sending.text
                color: controller.sending.error ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Controls.ProgressBar {
                visible: !controller.sending.error
                from: 0
                to: 1
                value: Math.max(controller.sending.progress, 0)
                indeterminate: controller.sending.busy && controller.sending.progress < 0
                Layout.fillWidth: true
            }
        }
        footer: Controls.DialogButtonBox {
            Controls.Button {
                objectName: "androidSendButton"
                text: controller.sending.busy ? qsTr("Cancel") : qsTr("Close")
                onClicked: controller.sending.busy ? controller.cancelSend() : sendDialog.close()
            }
        }
        Connections {
            target: controller
            function onSendChanged() {
                if (controller.sending.busy)
                    return;
                if (controller.sending.done)
                    sendClose.restart();  // "Done" stays a moment, then the window closes.
                else if (!controller.sending.error)
                    sendDialog.close();  // Cancelled.
            }
        }
        Timer {
            id: sendClose
            interval: 1500
            onTriggered: sendDialog.close()
        }
    }
    Controls.Dialog {
        id: microphoneDialog
        objectName: "androidMicrophoneDialog"
        parent: Controls.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(page.width - page.outerMargin * 2, Kirigami.Units.gridUnit * 29)
        title: qsTr("Camera and microphone: %1").arg(page.microphoneName)
        modal: true
        standardButtons: Controls.Dialog.Close
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            Kirigami.Heading {
                level: 4
                text: qsTr("Webcam")
            }
            Controls.Label {
                text: qsTr("Android sees only this webcam. A change applies when Android restarts.")
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Controls.ComboBox {
                    id: cameraChoice
                    objectName: "androidCameraChoice"
                    model: controller.cameras
                    textRole: "label"; valueRole: "name"
                    currentIndex: controller.cameraIndex
                    enabled: !controller.cameraBusy && count > 0
                    Layout.fillWidth: true
                    Accessible.name: qsTr("Webcam")
                    delegate: Controls.ItemDelegate {
                        required property var modelData
                        required property int index
                        width: ListView.view ? ListView.view.width : implicitWidth
                        text: modelData.label
                        enabled: modelData.enabled
                        highlighted: cameraChoice.highlightedIndex === index
                    }
                    onActivated: controller.setCamera(page.microphoneId, currentValue)
                }
                Controls.BusyIndicator {
                    running: controller.cameraBusy
                    visible: running
                    Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                }
            }
            Controls.Label {
                visible: text !== ""
                text: controller.cameraNote
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Controls.Label {
                visible: text !== ""
                text: controller.cameraError
                textFormat: Text.PlainText
                color: Kirigami.Theme.negativeTextColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Controls.Button {
                objectName: "androidCameraRestart"
                text: qsTr("Restart Android")
                icon.source: BreezeIcons.source("view-refresh", Kirigami.Theme.backgroundColor)
                enabled: !controller.busy
                onClicked: controller.actOn(page.microphoneId, "restart")
            }
            Kirigami.Heading {
                level: 4
                text: qsTr("Microphone")
            }
            Controls.Label {
                text: qsTr("Android applications record from this microphone. A change applies to the next recording.")
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Controls.ComboBox {
                    objectName: "androidMicrophoneChoice"
                    model: controller.microphones
                    textRole: "label"; valueRole: "name"
                    currentIndex: controller.microphoneIndex
                    enabled: !controller.microphoneBusy && count > 0
                    Layout.fillWidth: true
                    Accessible.name: qsTr("Microphone")
                    onActivated: controller.setMicrophone(page.microphoneId, currentValue)
                }
                Controls.BusyIndicator {
                    running: controller.microphoneBusy
                    visible: running
                    Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                }
            }
            Controls.Label {
                visible: text !== ""
                text: controller.microphoneError
                textFormat: Text.PlainText
                color: Kirigami.Theme.negativeTextColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }
    Dialogs.FileDialog {
        id: filePicker
        title: qsTr("Send files to %1").arg(page.sendName)
        fileMode: Dialogs.FileDialog.OpenFiles
        currentFolder: StandardPaths.writableLocation(StandardPaths.HomeLocation)
        onAccepted: {
            sendDialog.open();
            controller.sendFiles(page.sendId, selectedFiles);
        }
    }
    footer: Controls.ToolBar {
        id: footerBar
        visible: !page.embedded
        padding: page.outerMargin
        contentItem: RowLayout {
            Item { Layout.fillWidth: true }
            Controls.Button {
                text: qsTr("Back")
                enabled: !controller.busy
                onClicked: page.returnRequested()
            }
        }
    }
}
