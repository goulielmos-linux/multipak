import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import "BreezeIcons.js" as BreezeIcons

InformationWindow {
    id: about
    property string applicationTitle
    property string applicationDescription
    property url iconSource
    property string titleObjectName: "aboutApplicationTitle"
    property string licenseObjectName: "aboutLicenseButton"
    property string licenseDialogObjectName: "aboutLicenseDialog"
    preferredWidth: Kirigami.Units.gridUnit * 32
    preferredMaximumHeight: Kirigami.Units.gridUnit * 40
    closeText: qsTr("Close")
    color: Kirigami.Theme.backgroundColor
    onShown: {
        scrollContent.contentItem.contentY = 0;
        aboutInfo.refresh();
    }

    RowLayout {
        objectName: "aboutIdentityFrame"
        spacing: Kirigami.Units.largeSpacing * 2
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.largeSpacing
        Layout.bottomMargin: Kirigami.Units.largeSpacing
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

    FormCard.FormCard {
        FormCard.FormTextDelegate {
            objectName: "aboutCopyright"
            text: "© 2026 Goulielmos"
            textItem.textFormat: Text.PlainText
        }
        FormCard.FormDelegateSeparator {}
        FormCard.FormButtonDelegate {
            objectName: about.licenseObjectName
            text: "GNU GPLv3"
            onClicked: licenseDialog.open()
            Accessible.name: qsTr("View license")
        }
    }

    FormCard.FormHeader { title: qsTr("Authors") }
    FormCard.FormCard {
        objectName: "aboutAuthorsFrame"
        Repeater {
            model: aboutInfo.authors
            delegate: ColumnLayout {
                id: author
                required property var modelData
                required property int index
                spacing: 0
                Layout.fillWidth: true
                FormCard.FormDelegateSeparator { visible: author.index > 0 }
                FormCard.FormButtonDelegate {
                    objectName: author.index === 0 ? "aboutAuthorInfoButton" : "aboutAuthorInfoButton_" + author.index
                    text: author.modelData.name
                    icon.source: BreezeIcons.source("user-identity", Kirigami.Theme.backgroundColor)
                    onClicked: Qt.openUrlExternally(author.modelData.url)
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        FormCard.FormHeader {
            objectName: "aboutTechnicalInformationHeading"
            title: qsTr("Libraries in use")
            Layout.fillWidth: true
        }
        Controls.ToolButton {
            objectName: "aboutCopyInformationButton"
            text: qsTr("Copy information")
            icon.source: BreezeIcons.source("edit-copy", Kirigami.Theme.backgroundColor)
            onClicked: aboutInfo.copyInformation(about.applicationTitle)
        }
    }
    FormCard.FormCard {
        objectName: "aboutTechnicalInformation"
        Repeater {
            model: aboutInfo.environment
            delegate: FormCard.FormTextDelegate {
                required property var modelData
                text: modelData.name
                description: modelData.description
            }
        }
        FormCard.FormDelegateSeparator {}
        Repeater {
            model: aboutInfo.libraries
            delegate: ColumnLayout {
                id: library
                required property var modelData
                required property int index
                spacing: 0
                Layout.fillWidth: true
                FormCard.FormDelegateSeparator { visible: library.index > 0 }
                FormCard.FormButtonDelegate {
                    objectName: "aboutLibraryLicenseButton_" + library.modelData.name
                    text: library.modelData.name
                    description: library.modelData.description
                    contentItem: RowLayout {
                        spacing: Kirigami.Units.largeSpacing
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Kirigami.Units.smallSpacing
                            Controls.Label { text: library.modelData.name; Layout.fillWidth: true }
                            Controls.Label {
                                text: library.modelData.description
                                color: Kirigami.Theme.disabledTextColor
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                        Controls.Label { text: library.modelData.version }
                        Kirigami.Icon {
                            source: BreezeIcons.source("go-next-symbolic", Kirigami.Theme.backgroundColor)
                            implicitWidth: Kirigami.Units.iconSizes.small
                            implicitHeight: implicitWidth
                            Accessible.ignored: true
                        }
                    }
                    onClicked: Qt.openUrlExternally(library.modelData.licenseUrl)
                }
            }
        }
    }

    FormCard.FormCard {
        visible: aboutInfo.flatpak
        FormCard.FormTextDelegate {
            text: "Flatpak"
            description: qsTr("Distribution format.")
        }
    }

    FormCard.FormHeader { title: qsTr("Backend") }
    FormCard.FormCard {
        Repeater {
            model: aboutInfo.backends
            delegate: ColumnLayout {
                id: backend
                required property var modelData
                required property int index
                spacing: 0
                Layout.fillWidth: true
                FormCard.FormDelegateSeparator { visible: backend.index > 0 }
                FormCard.FormTextDelegate {
                    text: backend.modelData.name
                    description: backend.modelData.path
                    contentItem: RowLayout {
                        spacing: Kirigami.Units.largeSpacing
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Kirigami.Units.smallSpacing
                            RowLayout {
                                Controls.Label { text: backend.modelData.name }

                            }
                            Controls.Label {
                                text: backend.modelData.path
                                textFormat: Text.PlainText
                                color: Kirigami.Theme.disabledTextColor
                                wrapMode: Text.WrapAnywhere
                                Layout.fillWidth: true
                            }
                        }
                        Controls.Label { text: backend.modelData.version }
                    }
                }
            }
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
