import QtQuick

import DynamicIsland

Item {
    id: root

    property string placeholder: "Reply…"
    property color accent: Theme.text
    readonly property alias typing: input.activeFocus

    signal send(string text)

    implicitHeight: 34

    function clear() {
        input.text = ""
    }

    function submit() {
        if (input.text.trim().length === 0)
            return
        root.send(input.text)
        input.text = ""
    }

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: Theme.raised
        border.width: 1
        border.color: input.activeFocus
                      ? Theme.alpha(root.accent, 0.55)
                      : Theme.alpha(Theme.text, 0.06)

        Behavior on border.color {
            ColorAnimation { duration: Theme.fadeDuration }
        }
    }

    TextInput {
        id: input
        anchors.left: parent.left
        anchors.leftMargin: 14
        anchors.right: sendButton.left
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        color: Theme.text
        font.pixelSize: Theme.fontBody
        selectByMouse: true
        selectionColor: Theme.alpha(root.accent, 0.4)
        clip: true
        onAccepted: root.submit()

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.placeholder
            color: Theme.faint
            font: input.font
            visible: input.text.length === 0 && !input.activeFocus
        }
    }

    ActionButton {
        id: sendButton
        anchors.right: parent.right
        anchors.rightMargin: 3
        anchors.verticalCenter: parent.verticalCenter
        implicitHeight: parent.height - 6
        label: Strings.send
        accent: root.accent
        highlighted: input.text.trim().length > 0
        onClicked: root.submit()
    }
}
