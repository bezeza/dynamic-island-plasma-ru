/*
 * The island's own configuration, from inside the panel.
 *
 * The settings pages next to this file edit ~/.config/dynamic-island/config.json,
 * which belongs to the island and not to the widget — so reading is a plain
 * `cat` (the same trick main.qml uses for the history: QML's XMLHttpRequest
 * refuses file:// urls unless plasmashell is started with an environment
 * variable nobody is going to set), and writing goes over D-Bus to the island,
 * which owns the file and applies the change in the same breath.
 *
 * There is no apply button anywhere in these pages. Every control writes as it
 * is used, which is also why the dialog's own Apply stays grey: by the time it
 * could be pressed there is nothing left to save.
 */

import QtCore
import QtQuick

import org.kde.plasma.plasma5support as P5Support

Item {
    id: bridge

    /// The whole file, or {} while it is being read — or if it is not there.
    property var values: ({})

    /// False until the first read came back; the controls stay quiet until then.
    property bool ready: false

    /* Whether the island is actually on the bus. Reading the file works either
     * way, but writing does not: a change is handed to the island, and with
     * nobody there it goes nowhere — which is worth saying out loud rather
     * than letting somebody set five things that never happen. */
    property bool running: false

    readonly property string probeCommand:
        "gdbus call --session --dest org.freedesktop.Notifications"
        + " --object-path /org/freedesktop/Notifications"
        + " --method org.dynamicisland.Island.HistoryPath"

    readonly property string path:
        String(StandardPaths.writableLocation(StandardPaths.GenericConfigLocation))
            .replace(/^file:\/\//, "")
        + "/dynamic-island/config.json"

    readonly property string readCommand: "cat " + bridge.quoted(bridge.path)

    function quoted(text) {
        return "'" + String(text).replace(/'/g, "'\\''") + "'"
    }

    P5Support.DataSource {
        id: io
        engine: "executable"
        connectedSources: []

        onNewData: (source, data) => {
            disconnectSource(source)
            if (source === bridge.probeCommand) {
                // Any answer at all means somebody is serving our interface.
                bridge.running = (data["exit code"] || 0) === 0
                return
            }
            if (source !== bridge.readCommand) {
                return  // a write; the island answers with the id, nothing to read
            }
            try {
                const parsed = JSON.parse(data["stdout"] || "{}")
                bridge.values = (parsed && typeof parsed === "object") ? parsed : ({})
            } catch (error) {
                bridge.values = ({})  // no file yet, or half-written
            }
            bridge.ready = true
        }
    }

    function load() {
        io.disconnectSource(bridge.readCommand)
        io.connectSource(bridge.readCommand)
        io.disconnectSource(bridge.probeCommand)
        io.connectSource(bridge.probeCommand)
    }

    Component.onCompleted: load()

    /* One key at a time is all the pages ever need, and sending only what
     * changed means two settings dialogs open at once cannot overwrite each
     * other's work with a stale copy of the whole file. */
    function set(key, value) {
        const patch = {}
        patch[key] = value
        bridge.values = Object.assign({}, bridge.values, patch)
        io.connectSource("gdbus call --session"
            + " --dest org.freedesktop.Notifications"
            + " --object-path /org/freedesktop/Notifications"
            + " --method org.dynamicisland.Island.SetConfig "
            + bridge.quoted(JSON.stringify(patch)))
    }

    /// What the island uses when the file says nothing.
    function value(key, fallback) {
        const stored = bridge.values[key]
        return stored === undefined ? fallback : stored
    }

    function sources() {
        const list = bridge.values.sources
        return Array.isArray(list) ? list : []
    }

    function setSources(list) {
        bridge.set("sources", list)
    }
}
