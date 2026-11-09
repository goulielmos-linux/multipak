import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page
    objectName: "advancedPage"
    signal aboutRequested()
    signal returnRequested()
    signal cancelRequested()
    property bool alternativeExpanded: false
    readonly property var configuration: welcome.configuration
    readonly property real outerMargin: Kirigami.Units.largeSpacing * 2
    readonly property real preferredWindowWidth: Math.max(
        header.implicitWidth, footerActions.implicitWidth, Kirigami.Units.gridUnit * 29
    ) + outerMargin * 2
    readonly property real preferredWindowHeight: body.implicitHeight
        + topPadding + bottomPadding + advancedFooter.implicitHeight
    padding: outerMargin

    function revealDirectMethod() {
        const position = directAction.mapToItem(page.flickable.contentItem, 0, 0);
        page.ensureVisible(directAction, position.x - directAction.x, position.y - directAction.y);
    }

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

        PackageSummary {
            Layout.fillWidth: true
        }

        Kirigami.Heading {
            text: qsTr("Advanced settings")
            level: 2
            Layout.fillWidth: true
        }

        Kirigami.AbstractCard {
            Layout.fillWidth: true

            contentItem: ColumnLayout {
                spacing: Kirigami.Units.smallSpacing

                Controls.Label {
                    text: qsTr("Container base")
                    font.bold: true
                    Layout.fillWidth: true
                }

                Controls.RadioButton {
                    id: suggestedBase
                    objectName: "suggestedBaseButton"
                    text: qsTr("Use MultiPak's recommendation (%1)").arg(page.configuration.suggestedTitle)
                    checked: page.configuration.useSuggested
                    Layout.fillWidth: true
                    onClicked: page.configuration.chooseSuggested(true)
                }

                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.fillWidth: true
                    Layout.leftMargin: suggestedBase.leftPadding + suggestedBase.indicator.width + suggestedBase.spacing

                    Controls.Label {
                        objectName: "confidencePrefix"
                        text: qsTr("Distribution detection confidence:")
                    }

                    Controls.Label {
                        objectName: "confidenceValue"
                        text: welcome.confidence
                        font.bold: true
                        color: welcome.confidence === qsTr("High")
                            ? Kirigami.Theme.positiveTextColor
                            : welcome.confidence === qsTr("Medium")
                                ? Kirigami.Theme.neutralTextColor
                                : Kirigami.Theme.negativeTextColor
                    }

                    Controls.ToolButton {
                        objectName: "suggestionInfoButton"
                        icon.name: "help-about"
                        icon.width: Kirigami.Units.iconSizes.small
                        icon.height: Kirigami.Units.iconSizes.small
                        padding: Kirigami.Units.smallSpacing / 2
                        implicitWidth: icon.width + leftPadding + rightPadding
                        implicitHeight: icon.height + topPadding + bottomPadding
                        text: qsTr("About the recommended environment")
                        display: Controls.AbstractButton.IconOnly
                        onClicked: suggestionDialog.open()
                        Controls.ToolTip.visible: hovered
                        Controls.ToolTip.text: text
                        Accessible.name: text
                    }

                    Item {
                        Layout.fillWidth: true
                    }
                }

                Controls.RadioButton {
                    objectName: "manualBaseButton"
                    text: qsTr("Choose another distribution or version")
                    checked: !page.configuration.useSuggested
                    Layout.fillWidth: true
                    onClicked: page.configuration.chooseSuggested(false)
                }

                Controls.ComboBox {
                    objectName: "baseProfileCombo"
                    model: page.configuration.profileOptions
                    textRole: "title"
                    currentIndex: page.configuration.profileIndex
                    enabled: !page.configuration.useSuggested
                    Layout.fillWidth: true
                    Accessible.name: qsTr("Container distribution and version")
                    onActivated: index => page.configuration.chooseProfile(index)
                }
            }
        }

        Kirigami.AbstractCard {
            Layout.fillWidth: true

            contentItem: ColumnLayout {
                spacing: Kirigami.Units.largeSpacing

                Controls.Label {
                    text: qsTr("Container")
                    font.bold: true
                    Layout.fillWidth: true
                }

                Controls.ComboBox {
                    id: modeCombo
                    objectName: "containerModeCombo"
                    model: page.configuration.containerOptions
                    textRole: "label"
                    currentIndex: page.configuration.containerModeIndex
                    Layout.fillWidth: true
                    Accessible.name: qsTr("How to use the container")
                    onActivated: index => page.configuration.chooseContainerMode(model[index].value)

                    delegate: Controls.ItemDelegate {
                        required property var modelData
                        required property int index
                        width: modeCombo.width
                        text: modelData.label
                        enabled: modelData.available
                        highlighted: modeCombo.highlightedIndex === index
                    }
                }

                Controls.ComboBox {
                    objectName: "existingContainerCombo"
                    visible: page.configuration.containerMode === "existing"
                    model: page.configuration.existingOptions
                    textRole: "name"
                    currentIndex: page.configuration.existingIndex
                    Layout.fillWidth: true
                    Accessible.name: qsTr("Existing compatible container")
                    onActivated: index => page.configuration.chooseExisting(index)
                }

                Controls.TextField {
                    objectName: "customContainerName"
                    visible: page.configuration.containerMode === "custom"
                    text: page.configuration.customName
                placeholderText: qsTr("Container name")
                    maximumLength: 63
                    Layout.fillWidth: true
                    Accessible.name: qsTr("Custom container name")
                    onTextEdited: {
                        const position = cursorPosition;
                        page.configuration.setCustomName(text);
                        cursorPosition = position;
                    }
                }

                Controls.Label {
                    objectName: "configurationError"
                    text: page.configuration.errorMessage
                    visible: text.length > 0 && (page.configuration.containerMode !== "custom"
                        || page.configuration.customName.trim().length > 0)
                    color: Kirigami.Theme.negativeTextColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Controls.Label {
                    objectName: "containerDescription"
                    text: page.configuration.containerDescription
                    visible: text.length > 0
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

            }
        }
        Controls.ToolButton {
            objectName: "alternativeButton"
            text: page.alternativeExpanded ? qsTr("Hide alternative method") : qsTr("Show alternative method")
            icon.name: page.alternativeExpanded ? "arrow-up" : "arrow-down"
            Layout.alignment: Qt.AlignLeft
            onClicked: page.alternativeExpanded = !page.alternativeExpanded
        }

        Kirigami.AbstractCard {
            objectName: "systemCard"
            visible: page.alternativeExpanded
            enabled: welcome.packaging === "RPM"
            Layout.fillWidth: true

            contentItem: ColumnLayout {
                spacing: Kirigami.Units.largeSpacing

                Controls.Label {
                    objectName: "systemMethodTitle"
                    text: qsTr("Install directly on the system")
                    font.bold: true
                    color: enabled ? Kirigami.Theme.negativeTextColor : palette.windowText
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Controls.Label {
                    objectName: "systemUnavailableNotice"
                    visible: welcome.packaging !== "RPM"
                    text: qsTr("Not available for %1.").arg(welcome.packaging)
                    font.bold: true
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Controls.Label {
                    objectName: "systemMethodSummary"
                    visible: welcome.packaging === "RPM"
                    text: qsTr("Use only as a last resort.")
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Controls.Button {
                    id: directAction
                    objectName: "systemMethod"
                    text: qsTr("Continue with direct installation")
                    Layout.alignment: Qt.AlignRight
                    onClicked: welcome.chooseMethod("system")
                    onActiveFocusChanged: if (activeFocus) Qt.callLater(page.revealDirectMethod)
                }
            }
        }
    }

    footer: Controls.ToolBar {
        id: advancedFooter
        padding: page.outerMargin

        contentItem: RowLayout {
            id: footerActions
            spacing: Kirigami.Units.smallSpacing

            Item {
                Layout.fillWidth: true
            }

            Controls.Button {
                objectName: "advancedBackButton"
                text: qsTr("Back")
                onClicked: page.returnRequested()
            }

            Controls.Button {
                objectName: "advancedCancelButton"
                text: qsTr("Cancel")
                onClicked: page.cancelRequested()
            }

            Controls.Button {
                objectName: "advancedContinueButton"
                text: qsTr("Continue")
                highlighted: true
                enabled: page.configuration.valid
                Accessible.defaultButton: true
                onClicked: welcome.confirmConfiguration()
            }
        }
    }

    InformationWindow {
        id: suggestionDialog
        objectName: "suggestionDialog"
        transientParent: page.Window.window
        title: qsTr("Suggested environment")
        preferredWidth: Kirigami.Units.gridUnit * 28

        Controls.Label {
            objectName: "suggestionExplanation"
            text: welcome.confidenceExplanation
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

}
