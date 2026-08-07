/*
 * The pages behind "Configure Dynamic Island…".
 *
 * The first is the widget's own two settings; the other two are the island's,
 * edited here because this is where a person right-clicks when they want to
 * change something about it.
 */

import QtQuick

import org.kde.plasma.configuration

ConfigModel {
    ConfigCategory {
        name: i18n("Panel icon")
        icon: "preferences-desktop-icons"
        source: "ConfigGeneral.qml"
    }

    ConfigCategory {
        name: i18n("Island")
        icon: "preferences-desktop-notification"
        source: "ConfigIsland.qml"
    }

    ConfigCategory {
        name: i18n("Sources")
        icon: "internet-news-reader"
        source: "ConfigSources.qml"
    }
}
