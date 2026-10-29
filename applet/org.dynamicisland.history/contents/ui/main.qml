/*
 * The panel end of the island.
 *
 * The pill itself is deliberately forgetful, so this applet is where a
 * notification goes to be read a second time.  It does not talk to the island
 * to get the list — the island writes ~/.local/share/dynamic-island/history.json
 * and we simply read it, which keeps the panel working even while the island is
 * restarting.  Only emptying the history needs the running process, because it
 * also owns the little PNGs the entries point at.
 */

import QtCore
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.plasmoid
import org.kde.plasma.components as PC3
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    /* QtCore's StandardPaths hands out urls ("file:///home/…"); cat wants a
     * plain path. */
    readonly property string historyPath:
        String(StandardPaths.writableLocation(StandardPaths.GenericDataLocation))
            .replace(/^file:\/\//, "")
        + "/dynamic-island/history.json"

    readonly property string readCommand: "cat " + root.quoted(root.historyPath)

    readonly property bool hasEntries: entries.count > 0
    property string searchText: ""
    property string selectedApp: ""
    property var appNames: []
    property int visibleCount: 0
    readonly property var appOptions: [i18n("All applications")].concat(root.appNames)

    /// Seconds since the epoch, kept fresh so the rows can age their timestamps.
    property real now: 0

    preferredRepresentation: compactRepresentation
    Plasmoid.icon: Plasmoid.configuration.iconName

    toolTipMainText: "Dynamic Island"  // a name, not a phrase — never translated
    toolTipSubText: hasEntries
        ? i18np("%1 notification in the history",
                "%1 notifications in the history", entries.count)
        : i18n("Nothing in the history")

    ListModel {
        id: entries
    }

    /* Re-reading the whole file is cheap at a hundred entries, and it is the
     * only way to notice that the island trimmed or cleared it behind our back.
     * Poll faster while the list is on screen so a fresh notification does not
     * appear to arrive late. */
    Timer {
        interval: root.expanded ? 1000 : 5000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            root.now = Date.now() / 1000
            root.reload()
        }
    }

    /* Reading the file is the awkward part of this applet.  QML's
     * XMLHttpRequest refuses file:// urls unless QML_XHR_ALLOW_FILE_READ is set
     * in the environment, and nobody is going to set that for plasmashell — so
     * the file comes in through the executable engine instead. */
    P5Support.DataSource {
        id: reader
        engine: "executable"
        connectedSources: []

        onNewData: (source, data) => {
            disconnectSource(source)
            root.apply(root.parse(data["stdout"] || ""))
        }
    }

    P5Support.DataSource {
        id: runner
        engine: "executable"
        connectedSources: []

        onNewData: source => {
            disconnectSource(source)
            root.reload()  // show the result of whatever we just ran
        }
    }

    function reload() {
        // Reconnecting an already connected source would not run it again.
        reader.disconnectSource(root.readCommand)
        reader.connectSource(root.readCommand)
    }

    function parse(text) {
        if (!text) {
            return []  // no file yet, or nothing in it
        }
        try {
            const parsed = JSON.parse(text)
            return Array.isArray(parsed) ? parsed : []
        } catch (error) {
            return null  // half-written file; the next tick gets it whole
        }
    }

    function quoted(text) {
        return "'" + text.replace(/'/g, "'\\''") + "'"
    }

    function matches(entry) {
        if (!entry) return false
        const app = String(entry.appName || "")
        if (root.selectedApp && app !== root.selectedApp) return false
        const needle = root.searchText.trim().toLocaleLowerCase()
        if (!needle) return true
        return [entry.summary, entry.body, entry.appName].some(value =>
            String(value || "").toLocaleLowerCase().includes(needle))
    }

    function updateVisibleCount() {
        let count = 0
        for (let index = 0; index < entries.count; ++index) {
            if (root.matches(entries.get(index))) ++count
        }
        root.visibleCount = count
    }

    function refreshFilters() {
        const names = []
        for (let index = 0; index < entries.count; ++index) {
            const name = String(entries.get(index).appName || "")
            if (name && names.indexOf(name) < 0) names.push(name)
        }
        names.sort((a, b) => a.toLocaleLowerCase().localeCompare(b.toLocaleLowerCase()))
        root.appNames = names
        if (root.selectedApp && names.indexOf(root.selectedApp) < 0) root.selectedApp = ""
        root.updateVisibleCount()
    }

    /* Rebuilding the model on every tick would throw away the scroll position
     * and re-run the delegate animations, so only touch what actually moved.
     * The list is newest first and only ever grows at the front. */
    function apply(fresh) {
        if (fresh === null) {
            return  // unreadable this time round; keep what is on screen
        }
        if (fresh.length === entries.count) {
            const top = fresh.length === 0 ? null : fresh[0]
            if (top === null || (top.time === entries.get(0).time && top.entryId === entries.get(0).entryId)) {
                return
            }
        }
        while (entries.count > fresh.length) {
            entries.remove(entries.count - 1)
        }
        for (let index = 0; index < fresh.length; ++index) {
            const entry = {
                entryId: fresh[index].entryId || "",
                appName: fresh[index].appName || "",
                summary: fresh[index].summary || "",
                body: fresh[index].body || "",
                accent: fresh[index].accent || "",
                urgency: fresh[index].urgency || 0,
                time: fresh[index].time || 0,
                iconName: fresh[index].iconName || "",
                iconFile: fresh[index].iconFile || "",
                desktopFile: fresh[index].desktopFile || "",
                desktopEntry: fresh[index].desktopEntry || "",
                url: fresh[index].url || ""
            }
            if (index < entries.count) {
                entries.set(index, entry)
            } else {
                entries.append(entry)
            }
        }
        root.refreshFilters()
    }

    /* The island owns the little PNGs the entries point at, so it has to do the
     * deleting; we only ask.  If it is not running there is nothing to ask, and
     * the list stays as it is. */
    /* Opening is the island's job: it knows how to find the application and
     * how to ask KWin for a window that is only minimised. */
    function open(application) {
        if (!application) {
            return
        }
        runner.connectSource("gdbus call --session"
            + " --dest org.freedesktop.Notifications"
            + " --object-path /org/freedesktop/Notifications"
            + " --method org.dynamicisland.Island.Open " + root.quoted(application))
    }

    function clearHistory() {
        runner.connectSource("gdbus call --session"
            + " --dest org.freedesktop.Notifications"
            + " --object-path /org/freedesktop/Notifications"
            + " --method org.dynamicisland.Island.ClearHistory")
    }

    function removeHistoryEntry(entryId) {
        if (!entryId) return
        runner.connectSource("gdbus call --session"
            + " --dest org.freedesktop.Notifications"
            + " --object-path /org/freedesktop/Notifications"
            + " --method org.dynamicisland.Island.RemoveHistoryEntry "
            + root.quoted(entryId))
    }

    compactRepresentation: MouseArea {
        Layout.minimumWidth: Kirigami.Units.iconSizes.small
        Layout.minimumHeight: Kirigami.Units.iconSizes.small

        activeFocusOnTab: true
        hoverEnabled: true  // otherwise containsMouse never becomes true
        acceptedButtons: Qt.LeftButton
        onClicked: root.expanded = !root.expanded

        Kirigami.Icon {
            id: panelIcon
            anchors.fill: parent
            source: Plasmoid.configuration.iconName
            active: parent.containsMouse
        }

        Rectangle {
            id: badge
            visible: Plasmoid.configuration.showBadge && root.hasEntries

            width: Math.max(4, Math.round(panelIcon.width / 5))
            height: width
            radius: width / 2
            color: Kirigami.Theme.highlightColor

            anchors.right: panelIcon.right
            anchors.top: panelIcon.top
            anchors.margins: -Math.round(width / 6)

            border.width: Math.max(1, Math.round(width / 6))
            border.color: Kirigami.Theme.backgroundColor
        }
    }

    fullRepresentation: PlasmaExtras.Representation {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 18
        Layout.minimumHeight: Kirigami.Units.gridUnit * 14
        Layout.preferredWidth: Kirigami.Units.gridUnit * 23
        Layout.preferredHeight: Kirigami.Units.gridUnit * 26

        collapseMarginsHint: true

        header: PlasmaExtras.PlasmoidHeading {
            ColumnLayout {
                anchors.fill: parent
                spacing: Kirigami.Units.smallSpacing

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    PlasmaExtras.Heading {
                        level: 1
                        text: i18n("History")
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    PC3.ToolButton {
                        icon.name: "edit-clear-history"
                        text: i18n("Clear history")
                        display: PC3.AbstractButton.IconOnly
                        enabled: root.hasEntries
                        onClicked: root.clearHistory()
                        PC3.ToolTip.text: text
                        PC3.ToolTip.visible: hovered
                        PC3.ToolTip.delay: Kirigami.Units.toolTipDelay
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    PC3.TextField {
                        Layout.fillWidth: true
                        placeholderText: i18n("Search notifications")
                        clearButtonEnabled: true
                        onTextChanged: {
                            root.searchText = text
                            root.updateVisibleCount()
                        }
                    }
                    PC3.ComboBox {
                        Layout.fillWidth: true
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 9
                        model: root.appOptions
                        currentIndex: root.selectedApp
                            ? Math.max(0, root.appOptions.indexOf(root.selectedApp)) : 0
                        onActivated: index => {
                            root.selectedApp = index > 0 ? String(root.appOptions[index]) : ""
                            root.updateVisibleCount()
                        }
                    }
                }
            }
        }

        PC3.ScrollView {
            anchors.fill: parent
            contentWidth: availableWidth  // never scroll sideways

            ListView {
                id: list
                model: entries
                reuseItems: true
                currentIndex: -1
                spacing: 0

                delegate: HistoryRow {
                    width: list.width
                    now: root.now
                    visible: root.matches(model)
                    height: visible ? implicitHeight : 0
                    onRemoveRequested: entryId => root.removeHistoryEntry(entryId)
                    /* A row that carried a link — a feed entry, a webhook —
                     * opens the page itself: there is no application behind
                     * "heise online" to look for a window of.  Everything
                     * else, best first: the resolved desktop file, then the
                     * entry the notification carried, and failing both the
                     * name the application called itself — the island can
                     * still match a window against that. */
                    onActivated: model.url
                        ? Qt.openUrlExternally(model.url)
                        : root.open(model.desktopFile || model.desktopEntry
                                    || model.iconName || model.appName)
                }

                add: Transition {
                    NumberAnimation {
                        property: "opacity"
                        from: 0
                        to: 1
                        duration: Kirigami.Units.shortDuration
                    }
                }
            }
        }

        PlasmaExtras.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: !root.hasEntries
            iconName: "notifications"
            text: i18n("Nothing in the history")
            explanation: i18n("Notifications stay here after the island has taken them away.")
        }

        PlasmaExtras.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: root.hasEntries && root.visibleCount === 0
            iconName: "edit-find"
            text: i18n("No matching notifications")
            explanation: i18n("Try a different search or application filter.")
        }
    }
}
