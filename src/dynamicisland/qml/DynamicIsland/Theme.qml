pragma Singleton

import QtQuick

/*
 * What the island is made of: four palettes and the numbers that do not
 * change with them.
 *
 * The default is tuned for an OLED panel — the body is true black, so those
 * pixels are simply off, and every other surface is a very dark grey that
 * still reads as "raised" against it. That is the look this project is for,
 * but a black pill on a light desktop is a hole in the screen, so there are
 * three more:
 *
 *   amoled  true black, the one the island was designed around
 *   dark    a dark grey body, for panels where black would bloom or smear
 *   light   dark type on white
 *   system  whatever the desktop's colour scheme says, read off the palette
 *
 * `mode` is set from the configuration in main.qml rather than read here: a
 * QML singleton has no business reaching into the application's context.
 */
QtObject {
    id: theme

    /// One of "amoled", "dark", "light", "system".
    property string mode: "amoled"

    /// The desktop's own colours. On Plasma this is the active colour scheme.
    property SystemPalette desktop: SystemPalette { colorGroup: SystemPalette.Active }

    /* Which way round the palette runs. For the fixed modes it is a fact; for
     * the desktop's it is a question, and the answer is simply whether the
     * window colour is closer to white or to black. */
    readonly property bool bright: mode === "light"
        || (mode === "system" && theme.desktop.window.hsvValue > 0.5)

    readonly property color body: {
        switch (mode) {
        case "light":  return "#FFFFFF"
        case "dark":   return "#1B1B1F"
        case "system": return theme.desktop.window
        default:       return "#000000"
        }
    }

    /// A surface that has to be visible *on* the body: buttons, the reply field.
    readonly property color raised: {
        switch (mode) {
        case "light":  return "#ECECEF"
        case "dark":   return "#26262B"
        // Eight per cent of the type colour over the body: the same step the
        // fixed palettes make, whichever way round the scheme runs.
        case "system": return Qt.tint(theme.desktop.window,
                                      theme.alpha(theme.desktop.windowText, 0.08))
        default:       return "#141417"
        }
    }

    readonly property color raisedHover: {
        switch (mode) {
        case "light":  return "#E0E0E5"
        case "dark":   return "#313138"
        case "system": return Qt.tint(theme.desktop.window,
                                      theme.alpha(theme.desktop.windowText, 0.16))
        default:       return "#1F1F24"
        }
    }

    readonly property color text: {
        switch (mode) {
        case "light":  return "#1A1A1E"
        case "dark":   return "#FFFFFF"
        case "system": return theme.desktop.windowText
        default:       return "#FFFFFF"
        }
    }

    /// The body text under a headline, and anything else that steps back.
    readonly property color subtext: {
        switch (mode) {
        case "light":  return "#5A5A63"
        case "dark":   return "#A8A8B0"
        case "system": return Qt.tint(theme.desktop.window,
                                      theme.alpha(theme.desktop.windowText, 0.65))
        default:       return "#98989F"
        }
    }

    /// Placeholder text: there, but not asking for anything.
    readonly property color faint: {
        switch (mode) {
        case "light":  return "#8E8E97"
        case "dark":   return "#6A6A72"
        case "system": return Qt.tint(theme.desktop.window,
                                      theme.alpha(theme.desktop.windowText, 0.42))
        default:       return "#5A5A61"
        }
    }

    // A fill for rectangles that are only there to carry a border, and the
    // colour of a mask, where nothing but the alpha channel is ever read.
    readonly property color clear: Qt.rgba(0, 0, 0, 0)

    readonly property int fontTiny: 11
    readonly property int fontBody: 13
    readonly property int fontTitle: 15

    readonly property int padding: 10
    readonly property int gap: 7
    readonly property int iconSize: 32

    // Album artwork: big enough on a media card to read as a cover, small
    // enough in the player row to stay a hint beside the track name.
    // A card with nothing to press is a touch narrower, so the pill visibly
    // grows in both directions when buttons arrive.
    readonly property int plainTrim: 38

    readonly property int coverSize: 48
    readonly property int coverRadius: 12
    readonly property int coverThumb: 30
    readonly property int coverThumbRadius: 8

    // The artwork behind the text is a mood, not a picture: blurred well past
    // recognition, and pushed away from the type it sits under — darker on a
    // dark card, lighter on a light one, or the words stop being readable.
    readonly property real backdropOpacity: bright ? 0.38 : 0.52
    readonly property real backdropBlur: 1.8
    readonly property real backdropDim: bright ? 0.22 : -0.14
    readonly property real backdropSaturation: 0.45

    /* The morph is a spring, not a curve: the pill has mass, settles once and
     * does not ring. Width is livelier than height — a bouncing height would
     * shake the text inside, which is exactly what makes a copy look cheap.
     * Content never leads the shape; it fades in behind it, a beat later. */
    readonly property real springWidth: 3.4
    readonly property real dampingWidth: 0.66
    readonly property real springHeight: 4.0
    readonly property real dampingHeight: 0.88
    readonly property real morphMass: 1.25

    /* The shape renders first, the contents after it. A button that fades in
     * while the pill is still growing arrives before the notification it
     * belongs to, and that is what makes the whole thing look unfinished. */
    readonly property int contentDelay: 300
    readonly property int contentDuration: 300
    readonly property int stagger: 55

    // The signature morph: a touch of overshoot so the pill feels elastic.
    // Width keeps the pop; height does not, or the card wobbles every time a
    // line of text or a row of buttons changes its size.
    readonly property int morphDuration: 420
    readonly property int growDuration: 340
    readonly property int fadeDuration: 220
    readonly property real overshoot: 1.10

    // The way out: collapse to the capsule first, then leave over the edge.
    readonly property int collapseDelay: 340
    readonly property int leaveDuration: 420

    function alpha(source, amount) {
        return Qt.rgba(source.r, source.g, source.b, amount)
    }
}
