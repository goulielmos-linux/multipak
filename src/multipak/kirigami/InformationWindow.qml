import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Controls.ApplicationWindow {
    id: dialog
    visible: false
    modality: Qt.WindowModal
    flags: Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint | Qt.WindowCloseButtonHint
    color: palette.window
    property real preferredWidth: Kirigami.Units.gridUnit * 28
    property real contentPadding: Kirigami.Units.largeSpacing * 2
    property string closeText: "OK"
    property string closeObjectName: "informationCloseButton"
    readonly property bool opened: visible
    default property alias dialogData: body.data
    signal shown()

    readonly property int fittedWidth: Math.ceil(Math.min(
        preferredWidth, Screen.desktopAvailableWidth - contentPadding * 2
    ))
    readonly property int fittedHeight: Math.ceil(Math.min(
        body.implicitHeight + contentPadding * 2 + actionBar.implicitHeight,
        Screen.desktopAvailableHeight - Kirigami.Units.gridUnit * 2
    ))
    width: fittedWidth
    height: fittedHeight
    minimumWidth: fittedWidth
    maximumWidth: fittedWidth
    minimumHeight: fittedHeight
    maximumHeight: fittedHeight

    function open() {
        if (!visible && transientParent) {
            x = transientParent.x + Math.round((transientParent.width - width) / 2);
            y = transientParent.y + Math.round((transientParent.height - height) / 2);
        }
        show();
        raise();
        requestActivate();
        shown();
    }

    onVisibilityChanged: {
        if (visibility === Window.Maximized) {
            showNormal();
        }
    }

    property Item scrollContent: Controls.ScrollView {
        id: viewport
        parent: dialog.contentItem
        anchors.fill: parent
        contentWidth: availableWidth
        leftPadding: dialog.contentPadding
        rightPadding: dialog.contentPadding
        topPadding: dialog.contentPadding
        bottomPadding: dialog.contentPadding
        background: null
        clip: true

        ColumnLayout {
            id: body
            width: viewport.availableWidth
            spacing: Kirigami.Units.largeSpacing
        }
    }

    footer: Controls.ToolBar {
        id: actionBar
        padding: dialog.contentPadding
        contentItem: RowLayout {
            Item {
                Layout.fillWidth: true
            }
            Controls.Button {
                objectName: dialog.closeObjectName
                text: dialog.closeText
                highlighted: true
                onClicked: dialog.close()
            }
        }
    }

    Shortcut {
        sequence: "Escape"
        context: Qt.WindowShortcut
        onActivated: dialog.close()
    }
}
