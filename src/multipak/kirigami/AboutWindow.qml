import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

InformationWindow {
    id: about
    property string applicationTitle
    property string applicationDescription
    property url iconSource
    property string titleObjectName: "aboutApplicationTitle"
    property string licenseObjectName: "aboutLicenseButton"
    property string licenseDialogObjectName: "aboutLicenseDialog"
    preferredWidth: Kirigami.Units.gridUnit * 32
    color: Kirigami.Theme.backgroundColor
    onShown: aboutInfo.refresh()

    Controls.Frame {
        objectName: "aboutIdentityFrame"
        padding: Kirigami.Units.largeSpacing
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.largeSpacing
            RowLayout {
                spacing: Kirigami.Units.largeSpacing * 2
                Layout.fillWidth: true
                Image {
                    source: about.iconSource
                    sourceSize.width: Kirigami.Units.iconSizes.huge
                    sourceSize.height: Kirigami.Units.iconSizes.huge
                    Layout.preferredWidth: Kirigami.Units.iconSizes.huge
                    Layout.preferredHeight: Kirigami.Units.iconSizes.huge
                    Layout.alignment: Qt.AlignTop
                    fillMode: Image.PreserveAspectFit
                    Accessible.ignored: true
                }
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    Kirigami.Heading {
                        objectName: about.titleObjectName
                        text: about.applicationTitle
                        level: 2
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Controls.Label {
                        objectName: "aboutVersion"
                        text: aboutInfo.version
                        color: Kirigami.Theme.disabledTextColor
                    }
                    Controls.Label {
                        objectName: "aboutDescription"
                        text: about.applicationDescription
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
        }
    }

    Kirigami.Heading {
        objectName: "aboutCopyrightHeading"
        text: qsTr("Copyright")
        level: 3
        Layout.fillWidth: true
    }
    Controls.Label {
        objectName: "aboutCopyright"
        text: "© 2026 Goulielmos"
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
        Layout.leftMargin: Kirigami.Units.largeSpacing
        Layout.rightMargin: Kirigami.Units.largeSpacing
    }

    Kirigami.Heading {
        objectName: "aboutLicenseHeading"
        text: qsTr("License")
        level: 3
        Layout.fillWidth: true
    }
    Controls.ItemDelegate {
        id: licenseButton
        objectName: about.licenseObjectName
        text: "GNU GPLv3"
        display: Controls.AbstractButton.TextOnly
        padding: 0
        Layout.fillWidth: true
        Layout.leftMargin: Kirigami.Units.largeSpacing
        Layout.rightMargin: Kirigami.Units.largeSpacing
        contentItem: Controls.Label {
            text: licenseButton.text
            textFormat: Text.PlainText
            font.underline: licenseButton.visualFocus || licenseButton.hovered
        }
        background: null
        onClicked: licenseDialog.open()
        Accessible.name: qsTr("View license")
    }

    Kirigami.Heading {
        objectName: "aboutTechnicalInformationHeading"
        text: qsTr("Libraries in use")
        level: 3
        Layout.fillWidth: true
    }
    ColumnLayout {
        objectName: "aboutTechnicalInformation"
        Layout.fillWidth: true
        spacing: Kirigami.Units.largeSpacing
        Controls.Frame {
            padding: Kirigami.Units.largeSpacing
            Layout.fillWidth: true
            ColumnLayout {
                anchors.fill: parent
                spacing: Kirigami.Units.largeSpacing
                Repeater {
                    model: aboutInfo.libraries
                    delegate: RowLayout {
                        id: library
                        required property var modelData
                        Layout.fillWidth: true
                        Controls.Label {
                            text: library.modelData.name
                            Layout.fillWidth: true
                        }
                        Controls.Label { text: library.modelData.version }
                        Controls.ToolButton {
                            objectName: "aboutLibraryLicenseButton_" + library.modelData.name
                            text: qsTr("View the %1 license").arg(library.modelData.name)
                            icon.name: "license"
                            display: Controls.AbstractButton.IconOnly
                            Accessible.name: text
                            Controls.ToolTip.visible: hovered
                            Controls.ToolTip.text: text
                            onClicked: Qt.openUrlExternally(library.modelData.licenseUrl)
                        }
                        Controls.ToolButton {
                            objectName: "aboutLibraryWebsiteButton_" + library.modelData.name
                            text: qsTr("Visit the %1 website").arg(library.modelData.name)
                            icon.name: "globe"
                            display: Controls.AbstractButton.IconOnly
                            Accessible.name: text
                            Controls.ToolTip.visible: hovered
                            Controls.ToolTip.text: text
                            onClicked: Qt.openUrlExternally(library.modelData.website)
                        }
                    }
                }
            }
        }
        Kirigami.Heading { text: qsTr("Backend"); level: 3 }
        Controls.Frame {
            padding: Kirigami.Units.largeSpacing
            Layout.fillWidth: true
            ColumnLayout {
                anchors.fill: parent
                spacing: Kirigami.Units.largeSpacing * 2
                Repeater {
                    model: aboutInfo.backends
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.smallSpacing
                        RowLayout {
                            Layout.fillWidth: true
                            Controls.Label { text: modelData.name; Layout.fillWidth: true }
                            Controls.Label { text: modelData.version }
                        }
                        Controls.Label {
                            text: qsTr("Executable: %1").arg(modelData.path)
                            textFormat: Text.PlainText
                            color: Kirigami.Theme.disabledTextColor
                            wrapMode: Text.WrapAnywhere
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }
    }

    Kirigami.Heading {
        text: qsTr("Authors")
        level: 3
        Layout.topMargin: Kirigami.Units.smallSpacing
    }
    Controls.Frame {
        objectName: "aboutAuthorsFrame"
        padding: Kirigami.Units.smallSpacing
        leftPadding: Kirigami.Units.largeSpacing
        rightPadding: Kirigami.Units.largeSpacing
        Layout.fillWidth: true
        RowLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.largeSpacing
            Controls.Label { text: "Goulielmos" }
            Controls.ToolButton {
                objectName: "aboutAuthorInfoButton"
                text: qsTr("About the author")
                icon.name: "help-about"
                icon.width: Kirigami.Units.iconSizes.small
                icon.height: Kirigami.Units.iconSizes.small
                padding: Kirigami.Units.smallSpacing
                implicitWidth: icon.width + leftPadding + rightPadding
                implicitHeight: icon.height + topPadding + bottomPadding
                display: Controls.AbstractButton.IconOnly
                Accessible.name: text
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: text
                onClicked: authorDialog.open()
            }
            Item { Layout.fillWidth: true }
        }
    }

    InformationWindow {
        id: authorDialog
        objectName: "aboutAuthorDialog"
        transientParent: about
        title: qsTr("About the author")
        Controls.Label {
            objectName: "aboutAuthorBiography"
            text: qsTr("I'm an IT technician turned historian. This app is an adventure made possible in large part by GPT-6.1 Sol.")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    InformationWindow {
        id: licenseDialog
        objectName: about.licenseDialogObjectName
        transientParent: about
        title: "GNU GPLv3"
        Controls.ScrollView {
            clip: true
            contentWidth: availableWidth
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.gridUnit * 16
            Flickable {
                contentWidth: width
                contentHeight: licenseText.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                Controls.TextArea.flickable: Controls.TextArea {
                    id: licenseText
                    text: aboutInfo.licenseText
                    textFormat: TextEdit.PlainText
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                }
            }
        }
    }
}
