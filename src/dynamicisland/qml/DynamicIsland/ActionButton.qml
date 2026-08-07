import QtQuick

import DynamicIsland

Item {
    id: root

    property string label: ""
    property color accent: Theme.text
    property bool highlighted: false

    signal clicked()

    implicitWidth: Math.max(72, text.implicitWidth + 28)
    implicitHeight: 30

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: root.highlighted
               ? Theme.alpha(root.accent, mouse.containsMouse ? 0.34 : 0.22)
               : (mouse.containsMouse ? Theme.raisedHover : Theme.raised)
        border.width: 1
        border.color: root.highlighted
                      ? Theme.alpha(root.accent, 0.45)
                      : Theme.alpha(Theme.text, 0.06)

        Behavior on color {
            ColorAnimation { duration: Theme.fadeDuration }
        }
    }

    Text {
        id: text
        anchors.centerIn: parent
        text: root.label
        color: root.highlighted ? Theme.text : Theme.subtext
        font.pixelSize: Theme.fontBody
        font.weight: Font.Medium
        elide: Text.ElideRight
        width: Math.min(implicitWidth, root.width - 20)
        horizontalAlignment: Text.AlignHCenter
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }

    scale: mouse.pressed ? 0.96 : 1.0

    Behavior on scale {
        NumberAnimation { duration: 90; easing.type: Easing.OutQuad }
    }
}
