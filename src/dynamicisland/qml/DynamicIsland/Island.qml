import QtQuick
import QtQuick.Effects

import DynamicIsland

/*
 * The pill itself.
 *
 * A notification arrives as a small capsule that pops out of the top edge and
 * only then stretches open — that two-step morph is what makes it read as a
 * Dynamic Island rather than as a popup that happens to be rounded.  The card
 * inside is always laid out at its final width and simply gets unveiled by the
 * clipping pill, so the height never jumps around mid-animation.
 */
Item {
    id: root

    // -- state ---------------------------------------------------------
    readonly property int notificationCount: Notifications.count
    readonly property bool hasContent: notificationCount > 0
    readonly property int stackCount: Math.max(
        0, Math.min(notificationCount - 1, Cfg.values.stackDepth))

    property var front: null
    property bool expanded: false
    property bool surfaceNeeded: false
    property real dragOffset: 0

    readonly property color accent: front && front.accent
                                    ? front.accent : Cfg.values.fallbackAccent
    readonly property bool wantsKeyboard: card.replying

    // -- geometry -------------------------------------------------------
    /// Buttons or a reply field are what make a card "full size".
    readonly property bool frontHasTools: !!front && (
        (front.actions !== undefined && front.actions.length > 0)
        || front.hasReply === true)
    readonly property int openWidth:
        Cfg.values.expandedWidth - (frontHasTools ? 0 : Theme.plainTrim)
    // The capsule grows with its label instead of cutting the app name off.
    readonly property int compactWidth: Math.max(
        Cfg.values.compactMinWidth,
        Math.min(Cfg.values.compactMaxWidth, peekRow.implicitWidth + 44))
    readonly property int compactHeight: Cfg.values.compactHeight
    readonly property int openHeight: Math.max(
        compactHeight, Math.min(Cfg.values.maxHeight, card.implicitHeight))
    readonly property int stackPeek: stackCount * 5

    /* The pill has to look like it grows out of the bezel, not like a rounded
     * box parked below it. So the body is drawn taller than the island and the
     * extra height goes above the screen edge, where the top corners — and the
     * hairline along them — are simply cut off. Only the bottom is rounded,
     * which is what makes it read as part of the display. */
    readonly property int overhang: Cfg.values.topMargin > 0 ? 0 : Math.round(pillRadius)
    readonly property real pillRadius: expanded
                                       ? Math.min(Cfg.values.expandedRadius, height / 2)
                                       : height / 2

    width: expanded && hasContent ? openWidth : compactWidth
    height: expanded && hasContent ? openHeight : compactHeight
    scale: hasContent ? 1.0 : 0.92
    opacity: hasContent ? 1.0 : 0.0

    /* How far the pill sits above where it belongs. It arrives by sliding down
     * out of the screen edge and leaves the same way — but only after it has
     * collapsed back into its capsule, which is what the pause below is for. */
    readonly property real edgeShift: -(compactHeight + Cfg.values.topMargin + 24)
    property real entryShift: hasContent ? 0 : edgeShift
    transform: Translate { y: root.entryShift }

    Behavior on entryShift {
        SequentialAnimation {
            // Nothing to wait for on the way in; on the way out the capsule
            // needs a moment to close first.
            PauseAnimation { duration: root.hasContent ? 0 : Theme.collapseDelay }
            NumberAnimation {
                duration: Theme.leaveDuration
                easing.type: root.hasContent ? Easing.OutCubic : Easing.InCubic
            }
        }
    }

    Behavior on width {
        SpringAnimation {
            spring: Theme.springWidth
            damping: Theme.dampingWidth
            mass: Theme.morphMass
            epsilon: 0.25
        }
    }

    // Stiffer and better damped than the width: the height carries the text,
    // and text that keeps bouncing after the pill has settled reads as cheap.
    Behavior on height {
        SpringAnimation {
            spring: Theme.springHeight
            damping: Theme.dampingHeight
            mass: Theme.morphMass
            epsilon: 0.25
        }
    }

    Behavior on scale {
        NumberAnimation {
            duration: Theme.growDuration
            easing.type: Easing.OutBack
            easing.overshoot: 1.2
        }
    }

    Behavior on opacity {
        SequentialAnimation {
            PauseAnimation { duration: root.hasContent ? 0 : Theme.collapseDelay }
            NumberAnimation { duration: Theme.fadeDuration }
        }
    }

    Behavior on dragOffset {
        enabled: !dragArea.pressed
        NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
    }

    // -- model plumbing --------------------------------------------------
    // QAbstractListModel rows are not bindable one by one, so the front card is
    // re-read whenever the model tells us something moved.
    /* The last card stays on screen while the pill closes. Dropping the
     * contents the moment the model empties turns the outro into a blink:
     * there is nothing left to collapse around. */
    function refresh() {
        if (Notifications.count > 0) {
            front = Notifications.at(0)
        }
    }

    Component.onCompleted: {
        refresh()
        syncInputRegion()
    }

    Connections {
        target: Notifications
        function onCountChanged() { root.refresh() }
        function onDataChanged() { root.refresh() }
        function onModelReset() { root.refresh() }
        // promote() reorders with beginMoveRows and emits none of the three
        // above. Without this the volume bar and the track banner are moved to
        // the front of the model and never drawn: the island keeps showing
        // whatever notification happened to be there.
        function onRowsMoved() { root.refresh() }
    }

    onHasContentChanged: {
        if (hasContent) {
            hideTimer.stop()
            surfaceNeeded = true
            dragOffset = 0
            expandTimer.restart()
        } else {
            expandTimer.stop()
            // Give the row of buttons its moment to leave before the pill
            // starts closing around it — the mirror image of the way in.
            closeTimer.restart()
            hideTimer.restart()
        }
        syncInputRegion()
    }

    Timer {
        id: expandTimer
        interval: 260
        onTriggered: root.expanded = true
    }

    Timer {
        id: closeTimer
        interval: Math.round(Theme.contentDuration / 2)
        onTriggered: root.expanded = false
    }

    Timer {
        // Keeps the surface mapped until collapse, slide and fade are done.
        id: hideTimer
        interval: Math.round(Theme.contentDuration / 2) + Theme.collapseDelay
                  + Theme.leaveDuration + Theme.fadeDuration + 160
        onTriggered: root.surfaceNeeded = false
    }

    // -- input region ------------------------------------------------------
    function syncInputRegion() {
        if (!hasContent) {
            WindowTools.clearInputRegion()
            return
        }
        WindowTools.setInputRegion(Math.round(x), Math.round(y),
                                   Math.round(width),
                                   Math.round(height + stackPeek))
    }

    onXChanged: syncInputRegion()
    onYChanged: syncInputRegion()
    onWidthChanged: syncInputRegion()
    onHeightChanged: syncInputRegion()

    // -- the coloured bloom -------------------------------------------------
    // MultiEffect keeps drawing its source, so the rectangle is inset far
    // enough for the pill to cover it completely — otherwise its antialiased
    // rim would peek out as a hard coloured outline instead of a soft glow.
    Rectangle {
        id: glowSource
        anchors.fill: parent
        anchors.margins: 6
        anchors.topMargin: 6 - root.overhang
        radius: Math.max(0, root.pillRadius - 6)
        color: root.accent
        z: -11
    }

    MultiEffect {
        anchors.fill: glowSource
        source: glowSource
        autoPaddingEnabled: true
        blurEnabled: true
        blur: 1.0
        blurMax: 64
        blurMultiplier: 0.6
        opacity: Cfg.values.glowOpacity
        visible: Cfg.values.glowOpacity > 0
        z: -10
    }

    // -- the stack peeking out below ----------------------------------------
    Repeater {
        model: root.stackCount

        // Only the rounded bottom edge of each card shows; the rest hides
        // behind the pill, which is what makes it read as a stack.
        Rectangle {
            z: -1 - index
            anchors.horizontalCenter: parent.horizontalCenter
            height: root.pillRadius * 2
            y: root.height - height + (index + 1) * 8
            width: root.width - (index + 1) * 22
            radius: root.pillRadius
            color: Theme.body
            opacity: 1.0 - index * 0.25
            border.width: 1
            border.color: Theme.alpha(Theme.text, Cfg.values.strokeOpacity + 0.05)
        }
    }

    // -- drag / click --------------------------------------------------------
    HoverHandler {
        id: hover
        onHoveredChanged: {
            Controller.setHovered(hovered)
            WindowTools.log("hover " + hovered)
        }
    }

    MouseArea {
        id: dragArea
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        cursorShape: Qt.PointingHandCursor

        /* Where the finger went down, in the surface's coordinates rather than
         * the island's own. The island moves with the drag, so measuring
         * against itself is measuring against a moving ruler: every pixel the
         * pointer travels, the pill travels with it and the local coordinate
         * barely changes. A 65 px swipe read as 22 that way — under the
         * threshold, so nothing happened and the card stayed put. */
        property real pressY: 0
        property bool moved: false

        function surfaceY(mouse) {
            return dragArea.mapToItem(null, mouse.x, mouse.y).y
        }

        onPressed: (mouse) => {
            pressY = surfaceY(mouse)
            moved = false
            WindowTools.log("press at " + Math.round(mouse.x) + "," + Math.round(mouse.y))
        }

        onPositionChanged: (mouse) => {
            if (!pressed)
                return
            const delta = surfaceY(mouse) - pressY
            if (Math.abs(delta) > 4)
                moved = true
            // Only upwards — dragging down would fight the screen edge.
            root.dragOffset = Math.min(0, delta)
        }

        onReleased: (mouse) => {
            const target = root.front
            WindowTools.log("release, moved=" + moved + " drag=" + Math.round(root.dragOffset)
                            + " target=" + (target ? target.notificationId : "none"))
            if (target) {
                if (-root.dragOffset >= Cfg.values.dismissThreshold)
                    Controller.dismiss(target.notificationId)
                else if (!moved && mouse.button === Qt.MiddleButton)
                    Controller.dismiss(target.notificationId)
                else if (!moved)
                    Controller.activateDefault(target.notificationId)
            }
            root.dragOffset = 0
        }

        onCanceled: root.dragOffset = 0
    }

    // -- the pill ------------------------------------------------------------
    Rectangle {
        id: pill
        anchors.fill: parent
        anchors.topMargin: -root.overhang
        radius: root.pillRadius
        color: Theme.body
        clip: true
        border.width: 1
        border.color: Theme.alpha(Theme.text, Cfg.values.strokeOpacity)

        Behavior on radius {
            NumberAnimation { duration: Theme.morphDuration; easing.type: Easing.OutCubic }
        }

        // What the capsule shows during the brief moment before it opens.
        Item {
            id: peek
            anchors.fill: parent
            anchors.topMargin: root.overhang
            opacity: root.expanded ? 0.0 : 1.0
            visible: opacity > 0

            Behavior on opacity {
                NumberAnimation { duration: 140 }
            }

            Row {
                id: peekRow
                anchors.centerIn: parent
                spacing: 8

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 8
                    height: 8
                    radius: 4
                    color: root.accent
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.front ? (root.front.appName || "Benachrichtigung") : ""
                    color: Theme.subtext
                    font.pixelSize: Theme.fontTiny
                    font.weight: Font.DemiBold
                    font.capitalization: Font.AllUppercase
                    font.letterSpacing: 0.6
                    elide: Text.ElideRight
                    // Deliberately not derived from root.width — that would
                    // feed the capsule's own width back into itself.
                    width: Math.min(implicitWidth, Cfg.values.compactMaxWidth - 60)
                }
            }
        }

        // The card is always laid out at full width; the pill unveils it.
        NotificationCard {
            id: card
            width: root.openWidth
            anchors.horizontalCenter: parent.horizontalCenter
            // The pill starts above the screen edge; the card does not.
            y: root.overhang
            notification: root.front
            accent: root.accent
            showClose: hover.hovered && root.expanded
            leaving: !root.hasContent
            opacity: root.expanded ? 1.0 : 0.0
            visible: opacity > 0

            Behavior on opacity {
                NumberAnimation { duration: 240; easing.type: Easing.OutCubic }
            }

            onDismissRequested: {
                if (root.front)
                    Controller.dismiss(root.front.notificationId)
            }

            onActionRequested: (key) => {
                if (root.front)
                    Controller.invokeAction(root.front.notificationId, key)
            }

            onReplyRequested: (text) => {
                if (root.front)
                    Controller.sendReply(root.front.notificationId, text)
            }
        }
    }
}
