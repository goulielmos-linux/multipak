import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root
    objectName: "welcomeWindow"
    title: qsTr("Application Installer — MultiPak")
    visible: true
    readonly property bool installationBusy: welcome.state === "installing"
        || welcome.state === "cleaning"
    flags: Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint
           | Qt.WindowMinimizeButtonHint
           | (installationBusy ? 0 : Qt.WindowCloseButtonHint)

    readonly property var currentPage: legacyPage.item || pageStack.currentItem
    readonly property int fittedWidth: Math.ceil(Math.min(
        700, Screen.desktopAvailableWidth,
        currentPage ? currentPage.preferredWindowWidth : Kirigami.Units.gridUnit * 31
    ))
    readonly property int fittedHeight: Math.ceil(Math.min(
        Screen.desktopAvailableHeight - Kirigami.Units.gridUnit * 2,
        currentPage ? currentPage.preferredWindowHeight : Kirigami.Units.gridUnit * 16
    ))
    width: fittedWidth
    height: fittedHeight
    minimumWidth: fittedWidth
    maximumWidth: fittedWidth
    minimumHeight: fittedHeight
    maximumHeight: fittedHeight

    function restoreFittedSize() {
        if (!visible || !currentPage) {
            return;
        }
        // Native configure events may still carry the preceding page's geometry.
        if (width !== fittedWidth) {
            width = Qt.binding(() => root.fittedWidth);
        }
        if (height !== fittedHeight) {
            height = Qt.binding(() => root.fittedHeight);
        }
    }

    onFittedWidthChanged: Qt.callLater(restoreFittedSize)
    onFittedHeightChanged: Qt.callLater(restoreFittedSize)
    onWidthChanged: if (width !== fittedWidth) Qt.callLater(restoreFittedSize)
    onHeightChanged: if (height !== fittedHeight) Qt.callLater(restoreFittedSize)

    onVisibilityChanged: {
        if (visibility === Window.Maximized) {
            showNormal();
        }
    }

    onClosing: close => {
        if (installationBusy) {
            close.accepted = false;
        } else if (!welcome.canClose) {
            close.accepted = false;
            welcome.requestClose();
        }
    }

    function syncOperation() {
        if (!welcome.live) {
            return;
        }
        if (welcome.state === "ready") {
            pageStack.clear();
            pageStack.push(welcomeComponent);
        } else if (welcome.state === "success" || welcome.state === "cancelled") {
            pageStack.clear();
            pageStack.push(completionComponent);
        } else if (!pageStack.currentItem || pageStack.currentItem.objectName !== "operationPage") {
            pageStack.clear();
            pageStack.push(operationComponent);
        }
    }

    Component.onCompleted: {
        if (welcome.live) {
            syncOperation();
        }
    }

    function openMethod() {
        if (pageStack.depth === 1) {
            pageStack.push(welcome.isAndroid ? methodComponent
                : welcome.isAppImage ? appimageComponent : methodComponent);
        }
    }

    function openModes() {
        if (welcome.selectedMethod !== "container") {
            return;
        }
        openMethod();
    }

    function openEnvironment() {
        if (welcome.isAppImage) {
            openMethod();
            return;
        }
        if (welcome.selectedMethod !== "container") {
            return;
        }
        if (welcome.selectedMode === "advanced") {
            openAdvanced();
        } else {
            openMethod();
        }
        if (currentPage.objectName === "methodPage" || currentPage.objectName === "advancedPage") {
            pageStack.push(environmentComponent);
        }
    }

    function openAdvanced() {
        if (welcome.selectedMode !== "advanced") {
            return;
        }
        openMethod();
        if (pageStack.depth === 2) {
            pageStack.push(advancedComponent);
        }
    }

    function openSystem() {
        if (welcome.selectedMethod !== "system" || welcome.selectedMode !== "advanced") {
            return;
        }
        openAdvanced();
        if (currentPage.objectName === "advancedPage") {
            pageStack.push(systemComponent);
        }
    }

    function showPreview(message) {
        choiceDialog.summary = message;
        choiceDialog.open();
    }

    function openReview() {
        openEnvironment();
    }

    // Explicit visual preview only; never called by the installation request.
    function previewCompletion() {
        if (!welcome.live && currentPage.objectName !== "completionPage") {
            pageStack.push(completionComponent);
        }
    }

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
        installer: true
        onInstallerCloseRequested: root.close()
    }

    DistroboxDialog {
        id: distroboxDialog
        transientParent: root
    }

    pageStack.globalToolBar.style: Kirigami.ApplicationHeaderStyle.None
    pageStack.initialPage: welcomeComponent

    Component {
        id: welcomeComponent
        WelcomePage {
            onAboutRequested: aboutDialog.open()
        }
    }

    Component {
        id: operationComponent
        OperationPage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
        }
    }

    Component {
        id: methodComponent
        MethodPage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
            onReturnRequested: root.pageStack.pop()
            onCancelRequested: root.close()
        }
    }

    Component {
        id: advancedComponent
        AdvancedPage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
            onReturnRequested: root.pageStack.pop()
            onCancelRequested: root.close()
        }
    }

    Component {
        id: systemComponent
        SystemPage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
            onReturnRequested: {
                welcome.chooseMethod("container");
                root.pageStack.pop();
            }
            onCancelRequested: root.close()
        }
    }

    Component {
        id: environmentComponent
        EnvironmentPage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
            onReturnRequested: root.pageStack.pop()
            onCancelRequested: root.close()
        }
    }

    Component {
        id: androidComponent
        AndroidPage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
            onCancelRequested: root.close()
            onReturnRequested: root.pageStack.pop()
        }
    }

    Component {
        id: androidAdvancedComponent
        AndroidAdvancedPage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
            onCancelRequested: root.close()
            onReturnRequested: root.pageStack.pop()
        }
    }

    Component {
        id: appimageComponent
        AppImagePage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
            onReturnRequested: root.pageStack.pop()
            onCancelRequested: root.close()
        }
    }

    Component {
        id: completionComponent
        CompletionPage {
            parent: root.pageStack
            onAboutRequested: aboutDialog.open()
            onFinishRequested: root.close()
        }
    }

    AboutWindow {
        id: aboutDialog
        objectName: "aboutDialog"
        transientParent: root
        title: qsTr("About the Application Installer — MultiPak")
        applicationTitle: qsTr("MultiPak Installation Wizard")
        applicationDescription: qsTr("Install a variety of packages safely and easily.")
        iconSource: "assets/multipak-installer.svg"
    }

    InformationWindow {
        id: choiceDialog
        objectName: "choiceDialog"
        transientParent: root
        property string summary
        title: qsTr("Kirigami preview")
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.largeSpacing
            Controls.Label {
                text: choiceDialog.summary
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Controls.Label {
                text: qsTr("This preview does not start an installation.")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }

    Connections {
        target: welcome
        function onDistroboxRequired() {
            Qt.callLater(() => distroboxDialog.open());
        }
        function onStateChanged() { root.syncOperation(); }
        function onCloseRequested() { root.close(); }
        function onNotice(message) {
            messageDialog.message = message;
            messageDialog.open();
        }
        function onChoiceMade() {
            if (welcome.lastChoice === "continue") {
                root.openMethod();
            } else if (welcome.live) {
                welcome.searchStore();
            } else {
                root.showPreview(qsTr("You chose to search the app store."));
            }
        }
        function onMethodChosen() {
            if (welcome.selectedMethod === "container") {
                return;
            } else {
                root.openSystem();
            }
        }
        function onModeChosen() {
            if (welcome.isAndroid) {
                root.pageStack.push(welcome.selectedMode === "standard"
                    ? androidComponent : androidAdvancedComponent);
                return;
            }
            if (welcome.selectedMode === "standard") {
                root.openEnvironment();
            } else {
                root.openAdvanced();
            }
        }
        function onEnvironmentChosen() {
            if (welcome.isAndroid) {
                root.pageStack.push(androidComponent);
                return;
            }
            root.openReview();
        }
        function onInstallationRequested() {
            root.showPreview(qsTr("You confirmed the installation."));
        }
    }

    InformationWindow {
        id: messageDialog
        objectName: "messageDialog"
        transientParent: root
        property string message
        title: "MultiPak"
        preferredWidth: Kirigami.Units.gridUnit * 26
        Controls.Label {
            text: messageDialog.message
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
