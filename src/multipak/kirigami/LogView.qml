import QtQuick
import QtQuick.Controls as Controls

Controls.ScrollView {
    id: view
    property alias text: logText.text
    property alias logObjectName: logText.objectName
    property bool following: true
    clip: true
    contentWidth: availableWidth
    background: Rectangle {
        color: view.palette.base
        border.color: view.palette.mid
        radius: 4
    }

    function rememberPosition() {
        following = viewport.atYEnd;
    }

    function syncPosition() {
        if (!visible || !following || viewport.moving || verticalBar.pressed || wheelObserver.active)
            return;
        const end = Math.max(viewport.originY,
            viewport.originY + viewport.contentHeight - viewport.height);
        viewport.contentY = end;
    }

    onTextChanged: {
        if (text.length === 0) {
            following = true;
        }
        Qt.callLater(syncPosition);
    }
    onVisibleChanged: {
        if (visible) {
            following = true;
            Qt.callLater(syncPosition);
        }
    }

    Controls.ScrollBar.vertical: Controls.ScrollBar {
        id: verticalBar
        objectName: view.logObjectName + "ScrollBar"
        parent: view
        x: view.mirrored ? 0 : view.width - width
        y: view.topPadding
        height: view.availableHeight
        z: 1
        onPressedChanged: {
            if (pressed)
                view.following = false;
            else
                view.rememberPosition();
        }
        onPositionChanged: if (pressed) view.rememberPosition()
    }

    Flickable {
        id: viewport
        objectName: view.logObjectName + "Viewport"
        contentWidth: width
        contentHeight: logText.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        onContentHeightChanged: Qt.callLater(view.syncPosition)
        onHeightChanged: Qt.callLater(view.syncPosition)
        onMovementStarted: view.following = false
        onMovementEnded: view.rememberPosition()
        onContentYChanged: {
            if (moving || verticalBar.pressed)
                view.rememberPosition();
            else if (atYEnd)
                view.following = true;
        }

        WheelHandler {
            id: wheelObserver
            target: null
            blocking: false
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: {
                view.following = false;
                Qt.callLater(view.rememberPosition);
            }
            onActiveChanged: if (!active) view.rememberPosition()
        }

        Controls.TextArea {
            id: logText
            width: viewport.width
            height: Math.max(viewport.height, implicitHeight)
            background: null
            textFormat: TextEdit.PlainText
            readOnly: true
            selectByMouse: true
            selectByKeyboard: true
            wrapMode: TextEdit.Wrap
            Keys.onPressed: event => {
                if (event.modifiers & Qt.ShiftModifier) {
                    view.following = false;
                    event.accepted = false;
                    return;
                }
                let position = viewport.contentY;
                const end = Math.max(viewport.originY,
                    viewport.originY + viewport.contentHeight - viewport.height);
                switch (event.key) {
                case Qt.Key_Up: position -= lineMetrics.height; break;
                case Qt.Key_Down: position += lineMetrics.height; break;
                case Qt.Key_PageUp: position -= viewport.height; break;
                case Qt.Key_PageDown: position += viewport.height; break;
                case Qt.Key_Home: position = viewport.originY; break;
                case Qt.Key_End: position = end; break;
                default:
                    event.accepted = false;
                    return;
                }
                viewport.contentY = Math.max(viewport.originY, Math.min(position, end));
                view.rememberPosition();
                event.accepted = true;
            }
        }
        FontMetrics {
            id: lineMetrics
            font: logText.font
        }
    }
}
