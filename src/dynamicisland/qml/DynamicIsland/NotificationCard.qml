import QtQuick
import QtQuick.Effects

import DynamicIsland

/*
 * The contents of one notification. It knows nothing about the island's shape
 * or animation — it only reports its natural height and emits what the user did.
 */
Item {
    id: root

    property var notification: null
    property color accent: Theme.text
    property bool showClose: false
    readonly property alias replying: reply.typing

    signal dismissRequested()
    signal actionRequested(string key)
    signal replyRequested(string text)

    // The cover stands beside the column, so it can outgrow the text; every
    // other variant is as tall as whatever the column ended up being.
    implicitHeight: Math.max(column.implicitHeight,
                             showCover ? Theme.coverSize : 0) + 2 * Theme.padding

    readonly property var actions: notification && notification.actions ? notification.actions : []
    readonly property bool hasReply: !!(notification && notification.hasReply)
    readonly property int progress: notification && notification.progress !== undefined
                                    ? notification.progress : -1

    // -- what is playing ----------------------------------------------
    readonly property string kind: notification && notification.kind
                                   ? notification.kind : "notification"
    readonly property var media: notification && notification.media
                                 ? notification.media : ({})
    readonly property string cover: media && media.cover ? media.cover : ""
    /* A cover only counts once it is actually on screen. The url outlives the
     * picture it points at — a track that ends takes its artwork out of the
     * provider while a card still holds the address — and a card that believes
     * in a cover it cannot draw hides the app icon for nothing. */
    readonly property bool hasCover: cover.length > 0
                                     && backdropSource.status === Image.Ready

    /* A media card puts the artwork on the right, where the eye is not already
     * busy with the app icon — and drops the icon, which on such a card is the
     * very same cover. An on-screen display keeps its own layout instead and
     * only gains the small thumb in the player row below its bar. */
    readonly property bool showCover: hasCover && kind !== "osd"
    readonly property bool showPlayer: kind === "osd"
                                       && !!(media && (media.title || hasCover))

    /// Where the writing starts: past the app icon, or at the edge without one.
    readonly property int textIndent: showCover ? 0 : Theme.iconSize + Theme.gap

    /// Set while the island closes: the row of buttons leaves before the pill.
    property bool leaving: false

    // The backdrop has to end where the pill ends; square corners behind a
    // rounded body would show up as bright wedges.
    readonly property real cornerRadius: Math.min(Cfg.values.expandedRadius, height / 2)

    CloseButton {
        id: closeButton
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Theme.padding
        z: 10
        opacity: root.showClose ? 1 : 0
        enabled: root.showClose
        onClicked: root.dismissRequested()

        Behavior on opacity {
            NumberAnimation { duration: Theme.fadeDuration }
        }
    }

    // -- the album behind the text -------------------------------------
    /* The same MultiEffect the island uses for its glow, pointed at the cover:
     * blurred past recognition and dimmed, it lends the card the colour of the
     * record while the black body stays what the text is read against. */
    Image {
        id: backdropSource
        anchors.fill: parent
        source: root.cover
        // Never drawn itself — the effect below is the only thing that reads it.
        visible: false
        asynchronous: true
        cache: false
        fillMode: Image.PreserveAspectCrop
        // Unrecognisable at this size already; the blur only finishes the job,
        // and the texture stays cheap while the card animates.
        sourceSize.width: 64
        sourceSize.height: 64
    }

    // Only the alpha channel of a mask is ever read, so the colour is beside
    // the point; the layer is what turns it into a texture the effect can use.
    Rectangle {
        id: backdropMask
        anchors.fill: parent
        radius: root.cornerRadius
        color: Theme.text
        visible: false
        // No mask texture for the cards that will never ask for one.
        layer.enabled: root.hasCover
    }

    MultiEffect {
        anchors.fill: parent
        source: backdropSource
        visible: root.hasCover
        // Padding would let the blur bleed past the mask and out of the pill.
        autoPaddingEnabled: false
        blurEnabled: true
        blur: 1.0
        blurMax: 64
        blurMultiplier: Theme.backdropBlur
        brightness: Theme.backdropDim
        saturation: Theme.backdropSaturation
        maskEnabled: true
        maskSource: backdropMask
        maskThresholdMin: 0.5
        maskSpreadAtMin: 0.4
        opacity: Theme.backdropOpacity
    }

    Column {
        id: column
        x: Theme.padding
        // Only ever off the padding when a cover is the taller of the two: then
        // the two sit level instead of the text hanging from the top edge.
        y: Theme.padding + Math.max(
               0, ((root.showCover ? Theme.coverSize : 0) - implicitHeight) / 2)
        // The text yields the right-hand strip to the cover when there is one.
        width: root.width - 2 * Theme.padding
               - (root.showCover ? Theme.coverSize + Theme.gap : 0)
        spacing: Theme.gap

        // -- header ---------------------------------------------------
        Item {
            width: parent.width
            height: Math.max(Theme.iconSize, headline.implicitHeight)

            Image {
                id: icon
                anchors.left: parent.left
                anchors.top: parent.top
                width: Theme.iconSize
                height: Theme.iconSize
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                cache: false
                smooth: true
                /* On a card that shows its cover the icon is that same picture.
                 * Anything that did not load is not drawn either — an icon
                 * that failed leaves a hole where the coloured initial below
                 * would have said which application is talking. */
                visible: status === Image.Ready && !root.showCover
                source: root.notification ? (root.notification.iconSource || "") : ""
                sourceSize.width: Theme.iconSize * 2
                sourceSize.height: Theme.iconSize * 2
            }

            // Falls back to a coloured initial when the app shipped no artwork.
            Rectangle {
                anchors.fill: icon
                radius: width / 2
                visible: !icon.visible && !root.showCover
                color: Theme.alpha(root.accent, 0.18)
                border.width: 1
                border.color: Theme.alpha(root.accent, 0.4)

                Text {
                    anchors.centerIn: parent
                    text: root.notification && root.notification.appName
                          ? root.notification.appName.charAt(0).toUpperCase() : "!"
                    color: root.accent
                    font.pixelSize: Theme.fontTitle + 3
                    font.weight: Font.DemiBold
                }
            }

            Column {
                id: headline
                anchors.left: parent.left
                anchors.leftMargin: root.textIndent
                // The close button lives on the card now, not in this row, so
                // the headline keeps its distance from it by hand.
                anchors.right: parent.right
                anchors.rightMargin: root.showClose ? closeButton.width + Theme.gap : 0
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2

                Text {
                    width: parent.width
                    text: root.notification ? (root.notification.appName || "") : ""
                    color: Theme.alpha(root.accent, 0.95)
                    font.pixelSize: Theme.fontTiny
                    font.weight: Font.DemiBold
                    font.capitalization: Font.AllUppercase
                    font.letterSpacing: 0.6
                    elide: Text.ElideRight
                    visible: text.length > 0
                }

                Text {
                    width: parent.width
                    text: root.notification ? (root.notification.summary || "") : ""
                    color: Theme.text
                    font.pixelSize: Theme.fontTitle
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    maximumLineCount: 2
                    wrapMode: Text.Wrap
                    visible: text.length > 0
                }
            }

        }

        // -- body -----------------------------------------------------
        /* Indented to sit under the title rather than under the icon: a text
         * block starting further left than its own headline looks like it
         * belongs to something else. */
        Text {
            x: root.textIndent
            width: parent.width - root.textIndent
            text: root.notification ? (root.notification.body || "") : ""
            visible: text.length > 0
            color: Theme.subtext
            font.pixelSize: Theme.fontBody
            lineHeight: 1.25
            wrapMode: Text.Wrap
            textFormat: Text.StyledText
            maximumLineCount: 8
            elide: Text.ElideRight
            onLinkActivated: (link) => Qt.openUrlExternally(link)
        }

        // -- progress -------------------------------------------------
        Rectangle {
            width: parent.width
            height: 4
            radius: 2
            visible: root.progress >= 0
            color: Theme.raised

            Rectangle {
                width: parent.width * Math.max(0, Math.min(100, root.progress)) / 100
                height: parent.height
                radius: parent.radius
                color: root.accent

                Behavior on width {
                    NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
                }
            }
        }

        // -- player row -----------------------------------------------
        /* A volume display that arrives while something is playing brings the
         * track along with it. Only the line is drawn here — the buttons right
         * below are the card's own media actions, so they keep travelling
         * through Controller.invokeAction like every other action. */
        Item {
            id: player
            width: parent.width
            height: Math.max(Theme.coverThumb, trackLine.height)
            visible: root.showPlayer

            opacity: 0
            transform: Translate { id: playerShift; y: -Theme.gap }

            onVisibleChanged: if (visible) playerEntry.restart()
            Component.onCompleted: if (visible) playerEntry.start()

            SequentialAnimation {
                id: playerEntry
                PauseAnimation { duration: Theme.contentDelay }
                ParallelAnimation {
                    NumberAnimation {
                        target: player; property: "opacity"; to: 1.0
                        duration: Theme.contentDuration
                        easing.type: Easing.OutCubic
                    }
                    SpringAnimation {
                        target: playerShift; property: "y"; to: 0
                        spring: 4.0; damping: 0.55; mass: 0.9; epsilon: 0.1
                    }
                }
            }

            CoverArt {
                id: playerArt
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: Theme.coverThumb
                height: Theme.coverThumb
                radius: Theme.coverThumbRadius
                source: root.cover
                visible: root.hasCover
            }

            Item {
                id: trackLine
                anchors.left: playerArt.visible ? playerArt.right : parent.left
                anchors.leftMargin: playerArt.visible ? Theme.gap : 0
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                height: Math.max(trackTitle.implicitHeight, trackArtist.implicitHeight)

                Text {
                    id: trackTitle
                    anchors.left: parent.left
                    // The artist keeps whatever the title leaves over, but the
                    // title never eats the whole line on its own.
                    width: trackArtist.text.length > 0
                           ? Math.min(implicitWidth, parent.width * 0.6)
                           : Math.min(implicitWidth, parent.width)
                    text: root.media && root.media.title ? root.media.title : ""
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }

                Text {
                    id: trackArtist
                    anchors.left: trackTitle.right
                    anchors.leftMargin: text.length > 0 ? 6 : 0
                    anchors.right: parent.right
                    anchors.baseline: trackTitle.baseline
                    text: root.media && root.media.artist ? "· " + root.media.artist : ""
                    color: Theme.subtext
                    font.pixelSize: Theme.fontBody
                    elide: Text.ElideRight
                }
            }
        }

        // -- actions --------------------------------------------------
        /* The row settles in after the pill has started to grow, one button
         * after the next. Waiting for the shape and then staggering is the
         * whole difference between "it appeared" and "it arrived". */
        Flow {
            x: root.textIndent
            width: parent.width - root.textIndent
            spacing: 8
            visible: root.actions.length > 0

            Repeater {
                model: root.actions

                ActionButton {
                    id: button

                    label: modelData.label
                    accent: root.accent
                    highlighted: index === 0
                    onClicked: root.actionRequested(modelData.key)

                    opacity: 0
                    // A button faded out is a button that is not there: at
                    // opacity 0 the MouseArea would still take the click.
                    enabled: opacity > 0.01
                    transform: Translate { id: buttonShift; y: -Theme.gap }

                    Component.onCompleted: buttonEntry.start()

                    /* Out the way it came in, only quicker and in reverse:
                     * the last button to arrive is the first to go, and the
                     * pill starts closing once the row has cleared. */
                    onLeavingChanged: {
                        if (root.leaving) {
                            // Stop the way in before starting the way out, or
                            // a dismiss during the first frames leaves both
                            // running and the button fades up as it goes.
                            buttonEntry.stop()
                            buttonExit.start()
                        } else {
                            // Coming back. The delegate is reused when the
                            // next notification offers the same buttons, so
                            // Component.onCompleted does not run a second time
                            // — without this the row stays invisible for good.
                            buttonExit.stop()
                            buttonEntry.restart()
                        }
                    }

                    property bool leaving: root.leaving

                    SequentialAnimation {
                        id: buttonExit
                        PauseAnimation {
                            duration: (root.actions.length - 1 - index) * Math.round(Theme.stagger / 2)
                        }
                        ParallelAnimation {
                            NumberAnimation {
                                target: button; property: "opacity"; to: 0.0
                                duration: Math.round(Theme.contentDuration / 2)
                                easing.type: Easing.InCubic
                            }
                            NumberAnimation {
                                target: buttonShift; property: "y"; to: -Theme.gap
                                duration: Math.round(Theme.contentDuration / 2)
                                easing.type: Easing.InCubic
                            }
                        }
                    }

                    SequentialAnimation {
                        id: buttonEntry
                        PauseAnimation {
                            duration: Theme.contentDelay + index * Theme.stagger
                        }
                        ParallelAnimation {
                            NumberAnimation {
                                target: button; property: "opacity"; to: 1.0
                                duration: Theme.contentDuration
                                easing.type: Easing.OutCubic
                            }
                            SpringAnimation {
                                target: buttonShift; property: "y"; to: 0
                                spring: 4.0; damping: 0.5; mass: 0.9; epsilon: 0.1
                            }
                        }
                    }
                }
            }
        }

        // -- inline reply ---------------------------------------------
        ReplyField {
            id: reply
            width: parent.width
            visible: root.hasReply
            accent: root.accent
            placeholder: root.notification && root.notification.replyPlaceholder
                         ? root.notification.replyPlaceholder : Strings.reply
            onSend: (text) => root.replyRequested(text)
        }
    }

    // -- the cover itself ------------------------------------------------
    CoverArt {
        id: coverArt
        visible: root.showCover
        width: Theme.coverSize
        height: Theme.coverSize
        radius: Theme.coverRadius
        source: root.cover
        x: root.width - Theme.padding - width
        // Centred on the column rather than on the card, so the artwork stays
        // level with the text no matter how far a row of buttons stretches it.
        y: Theme.padding + Math.max(0, (column.implicitHeight - height) / 2)
    }

    /* QtQuick has no rounded image, so the artwork is drawn through the same
     * MultiEffect as everything else here and cut to shape by an alpha mask. */
    component CoverArt: Item {
        id: art

        property url source: ""
        property real radius: Theme.coverRadius

        Image {
            id: artSource
            anchors.fill: parent
            source: art.source
            visible: false
            asynchronous: true
            cache: false
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: art.width * 2
            sourceSize.height: art.height * 2
        }

        Rectangle {
            id: artMask
            anchors.fill: parent
            radius: art.radius
            color: Theme.text
            visible: false
            layer.enabled: true
        }

        MultiEffect {
            anchors.fill: parent
            source: artSource
            autoPaddingEnabled: false
            maskEnabled: true
            maskSource: artMask
            maskThresholdMin: 0.5
            maskSpreadAtMin: 0.4
        }

        // A hairline so a dark cover still has an edge against the black body.
        Rectangle {
            anchors.fill: parent
            radius: art.radius
            color: Theme.clear
            border.width: 1
            border.color: Theme.alpha(Theme.text, 0.08)
        }
    }
}
