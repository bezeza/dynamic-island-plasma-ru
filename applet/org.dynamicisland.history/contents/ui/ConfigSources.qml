/*
 * Feeds, endpoints and the webhook — the island's `sources` list.
 *
 * The list on the left is the file; the panel on the right edits one entry.
 * An entry is written back whole because that is how it is stored: one JSON
 * object per source, in one array.
 */

import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    id: root

    property int selected: -1
    property var draft: ({})

    IslandConfig {
        id: island
        onValuesChanged: root.refresh()
    }

    /// What a new source of each kind starts out as.
    readonly property var templates: ({
        "rss": {
            "type": "rss", "name": i18n("New feed"), "enabled": true, "url": "",
            "appName": "", "icon": "", "interval": 900,
            "maxItems": 3, "filter": "", "urgency": "low", "announceFirst": false
        },
        "rest": {
            "type": "rest", "name": i18n("New endpoint"), "enabled": true, "url": "",
            "appName": "", "icon": "network-server", "interval": 300,
            "maxItems": 3, "itemsPath": "", "announceFirst": false
        },
        "webhook": {
            "type": "webhook", "name": i18n("Webhook"), "enabled": true,
            "appName": "Webhook", "icon": "", "bind": "127.0.0.1", "port": 8757,
            "token": "", "allowCommands": false
        }
    })

    function refresh() {
        const entries = island.sources()
        entryList.clear()
        for (let index = 0; index < entries.length; ++index) {
            entryList.append({
                name: entries[index].name || entries[index].type || "source",
                type: entries[index].type || "",
                on: entries[index].enabled !== false
            })
        }
        if (root.selected >= entries.length)
            root.selected = entries.length - 1
        root.draft = root.selected >= 0 ? entries[root.selected] : ({})
    }

    function edit(key, value) {
        if (root.selected < 0)
            return
        const entries = island.sources()
        if (root.selected >= entries.length)
            return
        // A fresh object rather than a write into the one draft already holds:
        // assigning a `property var` the same reference back emits no change,
        // so every binding on root.draft.* keeps its old value. That is how a
        // generated webhook token stayed invisible in the very field the user
        // is told to copy it out of.
        const updated = JSON.parse(JSON.stringify(root.draft))
        updated[key] = value
        root.draft = updated
        entries[root.selected] = updated
        island.setSources(entries)
    }

    function append(kind) {
        const entries = island.sources()
        entries.push(JSON.parse(JSON.stringify(root.templates[kind])))
        island.setSources(entries)
        root.selected = entries.length - 1
        root.draft = entries[root.selected]
    }

    /* Long enough that guessing is not a strategy, and out of an alphabet
     * that survives being pasted into a shell, a YAML file and a URL. */
    function newToken() {
        const alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        let token = ""
        for (let index = 0; index < 32; ++index) {
            token += alphabet.charAt(Math.floor(Math.random() * alphabet.length))
        }
        return token
    }

    function remove() {
        const entries = island.sources()
        if (root.selected < 0 || root.selected >= entries.length)
            return
        entries.splice(root.selected, 1)
        island.setSources(entries)
        root.selected = -1
        root.draft = ({})
    }

    ListModel { id: entryList }

    ColumnLayout {
        anchors.fill: parent
        spacing: Kirigami.Units.smallSpacing

        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: island.ready && !island.running
            type: Kirigami.MessageType.Warning
            text: i18n("The island is not running. A source added here is handed to it, so it would go nowhere: systemctl --user start dynamic-island")
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Kirigami.Units.largeSpacing
            enabled: island.ready

            // -- the list --------------------------------------------------------
            ColumnLayout {
                Layout.preferredWidth: Kirigami.Units.gridUnit * 12
                Layout.minimumWidth: Kirigami.Units.gridUnit * 12
                Layout.maximumWidth: Kirigami.Units.gridUnit * 12
                Layout.fillHeight: true

                QQC2.Frame {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    padding: 1
                    contentItem: ListView {
                        id: list
                        model: entryList
                        clip: true
                        currentIndex: root.selected

                        delegate: QQC2.ItemDelegate {
                            width: ListView.view.width
                            highlighted: index === root.selected
                            onClicked: {
                                root.selected = index
                                root.draft = island.sources()[index]
                            }

                            contentItem: RowLayout {
                                QQC2.CheckBox {
                                    checked: model.on
                                    // A reused delegate is handed the next row's
                                    // value, which is not somebody toggling it.
                                    property bool ready: false
                                    Component.onCompleted: ready = true
                                    onToggled: {
                                        if (!ready)
                                            return
                                        const entries = island.sources()
                                        entries[index].enabled = checked
                                        island.setSources(entries)
                                    }
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    QQC2.Label {
                                        text: model.name
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                    }
                                    QQC2.Label {
                                        text: model.type
                                        opacity: 0.6
                                        font: Kirigami.Theme.smallFont
                                    }
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true

                    QQC2.Button {
                        text: i18n("Add")
                        icon.name: "list-add"
                        onClicked: addMenu.popup()

                        QQC2.Menu {
                            id: addMenu
                            QQC2.MenuItem {
                                text: i18n("RSS or Atom feed")
                                onTriggered: root.append("rss")
                            }
                            QQC2.MenuItem {
                                text: i18n("JSON endpoint")
                                onTriggered: root.append("rest")
                            }
                            QQC2.MenuItem {
                                text: i18n("Webhook")
                                onTriggered: root.append("webhook")
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }

                    QQC2.Button {
                        text: i18n("Remove")
                        icon.name: "list-remove"
                        enabled: root.selected >= 0
                        onClicked: root.remove()
                    }
                }
            }

            // -- the editor -------------------------------------------------------
            QQC2.ScrollView {
                id: editorScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: Kirigami.Units.gridUnit * 16
                // Otherwise the content is as wide as it likes and the view
                // grows a sideways scrollbar instead of wrapping the text.
                contentWidth: availableWidth
                clip: true

                ColumnLayout {
                    id: editor
                    width: editorScroll.availableWidth
                    spacing: Kirigami.Units.smallSpacing

                    readonly property string kind: root.draft.type || ""
                    readonly property bool polled: kind !== "webhook" && kind !== ""

                    QQC2.Label {
                        Layout.fillWidth: true
                        visible: root.selected < 0
                        wrapMode: Text.WordWrap
                        opacity: 0.7
                        text: i18n("Nothing selected. Add a feed, a JSON endpoint, or a webhook other machines can send notifications to.")
                    }

                    Field {
                        visible: root.selected >= 0
                        label: i18n("Name")
                        value: root.draft.name || ""
                        onCommitted: to => root.edit("name", to)
                    }

                    Field {
                        visible: root.selected >= 0
                        label: i18n("Shown as")
                        placeholder: i18n("the name above")
                        value: root.draft.appName || ""
                        onCommitted: to => root.edit("appName", to)
                    }

                    Field {
                        visible: root.selected >= 0
                        label: i18n("Icon")
                        placeholder: i18n("theme name or path")
                        value: root.draft.icon || ""
                        onCommitted: to => root.edit("icon", to)
                    }

                    // -- polled ------------------------------------------------
                    Field {
                        visible: editor.polled
                        label: i18n("Address")
                        placeholder: "https://example.com/feed.xml"
                        value: root.draft.url || ""
                        onCommitted: to => root.edit("url", to)
                    }

                    Count {
                        visible: editor.polled
                        label: i18n("Checked every")
                        unit: i18n(" s")
                        from: 30; to: 86400; step: 30
                        value: root.draft.interval || 300
                        onCommitted: to => root.edit("interval", to)
                    }

                    Count {
                        visible: editor.polled
                        label: i18n("At most per round")
                        from: 1; to: 20; step: 1
                        value: root.draft.maxItems || 3
                        onCommitted: to => root.edit("maxItems", to)
                    }

                    Check {
                        visible: editor.polled
                        label: i18n("Announce what is already there")
                        hint: i18n("Off keeps the first round quiet, so a restart does not replay old entries.")
                        value: root.draft.announceFirst === true
                        onCommitted: to => root.edit("announceFirst", to)
                    }

                    Field {
                        visible: ["rss", "atom", "feed"].indexOf(editor.kind) >= 0
                        label: i18n("Only headlines matching")
                        placeholder: i18n("a regular expression, empty for all")
                        value: root.draft.filter || ""
                        onCommitted: to => root.edit("filter", to)
                    }

                    Field {
                        visible: ["rest", "json", "api"].indexOf(editor.kind) >= 0
                        label: i18n("Where the list is in the answer")
                        placeholder: "data.incidents"
                        value: root.draft.itemsPath || ""
                        onCommitted: to => root.edit("itemsPath", to)
                    }

                    Pairs {
                        visible: ["rest", "json", "api"].indexOf(editor.kind) >= 0
                        label: i18n("Fields")
                        hint: i18n("island field: path in the answer — one per line")
                        placeholder: "summary: name\nbody: status\nid: id\nurl: link"
                        value: root.draft.fields || ({})
                        onCommitted: to => root.edit("fields", to)
                    }

                    Pairs {
                        visible: ["rest", "json", "api"].indexOf(editor.kind) >= 0
                        label: i18n("Request headers")
                        hint: i18n("header: value — one per line")
                        placeholder: "Authorization: Bearer …"
                        value: root.draft.headers || ({})
                        onCommitted: to => root.edit("headers", to)
                    }

                    // -- webhook -----------------------------------------------
                    Field {
                        visible: editor.kind === "webhook"
                        label: i18n("Listens on")
                        placeholder: "127.0.0.1"
                        value: root.draft.bind || ""
                        onCommitted: to => root.edit("bind", to)
                    }

                    QQC2.Label {
                        visible: editor.kind === "webhook"
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        opacity: 0.7
                        font: Kirigami.Theme.smallFont
                        text: i18n("127.0.0.1 is this machine only. 0.0.0.0 opens it to the network, and the island then refuses to start without a token.")
                    }

                    Count {
                        visible: editor.kind === "webhook"
                        label: i18n("Port")
                        from: 1; to: 65535; step: 1
                        value: root.draft.port || 8757
                        onCommitted: to => root.edit("port", to)
                    }

                    Field {
                        visible: editor.kind === "webhook"
                        label: i18n("Token")
                        placeholder: i18n("empty means no authorisation")
                        secret: true
                        value: root.draft.token || ""
                        onCommitted: to => root.edit("token", to)
                    }

                    RowLayout {
                        visible: editor.kind === "webhook"
                        Layout.fillWidth: true

                        QQC2.Button {
                            text: i18n("Generate")
                            icon.name: "roll"
                            onClicked: root.edit("token", root.newToken())
                        }

                        QQC2.Label {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            font: Kirigami.Theme.smallFont
                            opacity: 0.7
                            text: i18n("The token is kept in config.json, which is written for you alone (mode 600). To keep it out of the file entirely, set \"tokenFile\" or \"tokenEnv\" there instead — see the README.")
                        }
                    }

                    Check {
                        visible: editor.kind === "webhook"
                        label: i18n("Buttons may run commands")
                        hint: i18n("Off is the safe setting: whoever can reach this port could otherwise run anything as you.")
                        value: root.draft.allowCommands === true
                        onCommitted: to => root.edit("allowCommands", to)
                    }

                    ColumnLayout {
                        visible: editor.kind === "webhook"
                        Layout.fillWidth: true
                        spacing: 2

                        QQC2.Label {
                            text: i18n("Anything that can fetch a URL can put a card up:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.7
                        }

                        QQC2.TextArea {
                            id: example
                            Layout.fillWidth: true
                            readOnly: true
                            wrapMode: Text.Wrap
                            font.family: "monospace"
                            text: {
                                const host = (root.draft.bind === "0.0.0.0"
                                              || root.draft.bind === "::")
                                             ? i18n("<this-machine>")
                                             : (root.draft.bind || "127.0.0.1")
                                const port = root.draft.port || 8757
                                const token = root.draft.token
                                              ? "&token=" + root.draft.token : ""
                                return "http://" + host + ":" + port + "/notify/backup"
                                     + "?summary=Backup%20finished"
                                     + "&body=42%20GB%20in%2011%20minutes" + token
                            }
                        }

                        RowLayout {
                            QQC2.Button {
                                text: i18n("Copy")
                                icon.name: "edit-copy"
                                onClicked: {
                                    example.selectAll()
                                    example.copy()
                                    example.deselect()
                                }
                            }
                            QQC2.Label {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                font: Kirigami.Theme.smallFont
                                opacity: 0.7
                                text: i18n("The last part of the path is what the card says it came from — /notify/backup, /notify/doorbell, one per service. POST with a JSON body works the same way; GET / on the port prints the whole format.")
                            }
                        }
                    }
                }
            }
        }
    }

    // -- the controls the editor is made of ---------------------------------
    component Field: ColumnLayout {
        id: field
        property string label: ""
        property string placeholder: ""
        property string value: ""
        /// Shown as dots until it is being typed — a token on a screen is a
        /// token in whatever was recording that screen.
        property bool secret: false
        signal committed(var to)

        Layout.fillWidth: true
        spacing: 0

        QQC2.Label { text: field.label; font: Kirigami.Theme.smallFont; opacity: 0.8 }

        QQC2.TextField {
            id: input
            Layout.fillWidth: true
            placeholderText: field.placeholder
            echoMode: field.secret && !reveal.checked
                      ? TextInput.Password : TextInput.Normal
            text: field.value
            // The new text goes out with the signal rather than into
            // field.value: the use site *binds* value to the draft, and an
            // assignment here would replace that binding for good — after
            // which this control would keep showing the old source's text
            // when a different one is picked.
            onEditingFinished: {
                if (text !== field.value)
                    field.committed(text)
            }

            QQC2.ToolButton {
                id: reveal
                visible: field.secret
                checkable: true
                icon.name: checked ? "password-show-off" : "password-show-on"
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.rightMargin: Kirigami.Units.smallSpacing
            }
            // Typing replaces the binding that filled this, so selecting a
            // different source would leave the old text on screen.
            Connections {
                target: field
                function onValueChanged() {
                    if (!input.activeFocus)
                        input.text = field.value
                }
            }
        }
    }

    component Count: ColumnLayout {
        id: count
        property string label: ""
        property string unit: ""
        property int from: 0
        property int to: 1000000
        property int step: 1
        property int value: 0
        property bool ready: false
        signal committed(var to)

        Layout.fillWidth: true
        spacing: 0
        Component.onCompleted: ready = true

        QQC2.Label { text: count.label; font: Kirigami.Theme.smallFont; opacity: 0.8 }

        QQC2.SpinBox {
            id: box
            from: count.from
            to: count.to
            stepSize: count.step
            editable: true
            value: count.value
            textFromValue: function (value) { return value + count.unit }
            valueFromText: function (text) { return parseInt(text) || count.from }
            onValueModified: {
                if (!count.ready)
                    return
                count.committed(value)
            }
            Connections {
                target: count
                function onValueChanged() { box.value = count.value }
            }
        }
    }

    component Check: ColumnLayout {
        id: check
        property string label: ""
        property string hint: ""
        property bool value: false
        property bool ready: false
        signal committed(var to)

        Layout.fillWidth: true
        spacing: 0
        Component.onCompleted: ready = true

        QQC2.CheckBox {
            id: box
            text: check.label
            checked: check.value
            onToggled: {
                if (!check.ready)
                    return
                check.committed(checked)
            }
            Connections {
                target: check
                function onValueChanged() { box.checked = check.value }
            }
        }

        QQC2.Label {
            visible: check.hint.length > 0
            Layout.fillWidth: true
            text: check.hint
            wrapMode: Text.WordWrap
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }
    }

    /* A dictionary, written the way anybody would write one by hand. Lines
     * that are not "name: value" are dropped rather than reported: this is a
     * text box, and half-typed lines are the normal state of one. */
    component Pairs: ColumnLayout {
        id: pairs
        property string label: ""
        property string hint: ""
        property string placeholder: ""
        property var value: ({})
        signal committed(var to)

        function written() {
            const lines = []
            for (const key in pairs.value)
                lines.push(key + ": " + pairs.value[key])
            return lines.join("\n")
        }

        Layout.fillWidth: true
        spacing: 0

        QQC2.Label { text: pairs.label; font: Kirigami.Theme.smallFont; opacity: 0.8 }
        QQC2.Label {
            visible: pairs.hint.length > 0
            text: pairs.hint
            font: Kirigami.Theme.smallFont
            opacity: 0.6
        }

        QQC2.TextArea {
            id: area
            Layout.fillWidth: true
            Layout.minimumHeight: Kirigami.Units.gridUnit * 4
            placeholderText: pairs.placeholder
            text: pairs.written()

            onEditingFinished: {
                const result = {}
                const lines = text.split("\n")
                for (let index = 0; index < lines.length; ++index) {
                    const at = lines[index].indexOf(":")
                    if (at <= 0)
                        continue
                    const key = lines[index].substring(0, at).trim()
                    const value = lines[index].substring(at + 1).trim()
                    if (key.length > 0 && value.length > 0)
                        result[key] = value
                }
                pairs.committed(result)
            }

            Connections {
                target: pairs
                function onValueChanged() {
                    if (!area.activeFocus)
                        area.text = pairs.written()
                }
            }
        }
    }
}
