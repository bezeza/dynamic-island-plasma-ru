/*
 * One past notification.
 *
 * The stripe on the left carries the same accent the island derived from the
 * app icon, so a row is recognisable as "that green one from before" before a
 * single word has been read.
 */

import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PC3
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami

Item {
    id: row

    /// Seconds since the epoch, handed down so every row ages at the same time.
    property real now: 0

    /// Emitted on a click: the reader wants the application that sent this.
    signal activated()
    signal removeRequested(string entryId)

    readonly property bool critical: model.urgency === 2
    readonly property color accent: critical
        ? Kirigami.Theme.negativeTextColor
        : (model.accent ? model.accent : Kirigami.Theme.highlightColor)

    // Rows need room to breathe: three lines of text plus the separator run
    // into each other at the theme's small spacing.
    implicitHeight: layout.implicitHeight + Kirigami.Units.largeSpacing * 2
    height: implicitHeight

    // A whole row is the click target; a past notification is only worth
    // anything if it takes you back to what sent it.
    MouseArea {
        id: hover
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: row.activated()
    }

    Rectangle {
        anchors.fill: parent
        anchors.bottomMargin: 1
        radius: Kirigami.Units.smallSpacing
        color: Kirigami.Theme.textColor
        opacity: hover.containsMouse ? 0.07 : 0
        z: -1

        Behavior on opacity {
            NumberAnimation { duration: Kirigami.Units.shortDuration }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.leftMargin: Kirigami.Units.smallSpacing
        anchors.verticalCenter: parent.verticalCenter
        width: 3
        height: parent.height - Kirigami.Units.largeSpacing
        radius: width / 2
        color: row.accent
        opacity: 0.9
    }

    RowLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: Kirigami.Units.largeSpacing
        anchors.leftMargin: Kirigami.Units.gridUnit
        spacing: Kirigami.Units.largeSpacing

        Kirigami.Icon {
            source: row.iconSource(model.iconFile, model.iconName)
            fallback: "dialog-information"
            // Kirigami.Icon sizes itself from the image, which is nothing at
            // all while a theme icon is still resolving — pin the column.
            Layout.preferredWidth: Kirigami.Units.iconSizes.medium
            Layout.preferredHeight: Kirigami.Units.iconSizes.medium
            Layout.alignment: Qt.AlignTop
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Math.round(Kirigami.Units.smallSpacing / 2)

            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                PC3.Label {
                    text: model.summary || model.appName
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    Layout.fillWidth: true
                }

                PlasmaExtras.DescriptiveLabel {
                    text: row.age(model.time, row.now)
                    font: Kirigami.Theme.smallFont
                }

                PC3.ToolButton {
                    z: 2
                    icon.name: "edit-delete"
                    text: i18n("Remove notification")
                    display: PC3.AbstractButton.IconOnly
                    enabled: !!model.entryId
                    onClicked: row.removeRequested(model.entryId || "")
                    PC3.ToolTip.text: text
                    PC3.ToolTip.visible: hovered
                    PC3.ToolTip.delay: Kirigami.Units.toolTipDelay
                }
            }

            PC3.Label {
                text: model.body
                visible: text.length > 0
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                elide: Text.ElideRight
                maximumLineCount: 3
                lineHeight: 1.15
                opacity: 0.85
                textFormat: Text.PlainText
            }

            PlasmaExtras.DescriptiveLabel {
                text: model.appName
                visible: text.length > 0 && text !== model.summary
                font: Kirigami.Theme.smallFont
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }
    }

    Kirigami.Separator {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Kirigami.Units.gridUnit
        opacity: 0.4
    }

    /* A stored bitmap wins: it is what the notification actually showed. The
     * name beside it is a theme name most of the time, but an ``image-path``
     * hint may just as well be an absolute path, and Kirigami.Icon only takes
     * that as a url. */
    function iconSource(file, name) {
        if (file) {
            return "file://" + file
        }
        if (!name) {
            return "dialog-information"
        }
        return name.charAt(0) === "/" ? "file://" + name : name
    }

    /// "5 minutes ago" while it is still fresh, a clock time once it is not.
    function age(seconds, reference) {
        if (!seconds) {
            return ""
        }
        const elapsed = Math.max(0, (reference || seconds) - seconds)
        if (elapsed < 60) {
            return i18n("just now")
        }
        if (elapsed < 3600) {
            const minutes = Math.floor(elapsed / 60)
            return i18np("%1 minute ago", "%1 minutes ago", minutes)
        }
        const stamp = new Date(seconds * 1000)
        if (elapsed < 86400 && stamp.getDate() === new Date(reference * 1000).getDate()) {
            return Qt.formatTime(stamp, Qt.locale(), Locale.ShortFormat)
        }
        return Qt.formatDateTime(stamp, Qt.locale(), Locale.ShortFormat)
    }
}
