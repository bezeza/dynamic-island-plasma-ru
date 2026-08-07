import QtQuick

import DynamicIsland

Item {
    id: root

    signal clicked()

    implicitWidth: 22
    implicitHeight: 22

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: mouse.containsMouse ? Theme.raisedHover : Theme.raised
        border.width: 1
        border.color: Theme.alpha(Theme.text, 0.06)

        Behavior on color {
            ColorAnimation { duration: Theme.fadeDuration }
        }
    }

    // A hand-drawn cross keeps us independent of whatever icon theme is loaded.
    Item {
        anchors.centerIn: parent
        width: 9
        height: 9

        Repeater {
            model: [45, -45]

            Rectangle {
                anchors.centerIn: parent
                width: parent.width
                height: 1.4
                radius: height / 2
                color: mouse.containsMouse ? Theme.text : Theme.subtext
                rotation: modelData
            }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        anchors.margins: -4
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
