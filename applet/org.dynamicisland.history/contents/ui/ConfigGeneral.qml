/*
 * The widget's settings page.
 *
 * Plasma drives this by naming convention: for every entry in main.xml it binds
 * cfg_<entry>, and it expects a cfg_<entry>Default alongside so the "restore
 * defaults" button has something to restore to.  The root has to be a KCM, not
 * a bare FormLayout, or the settings window has nothing to put a title on.
 */

import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.iconthemes as KIconThemes
import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    id: root

    property alias cfg_iconName: iconField.text
    property string cfg_iconNameDefault: "notifications"

    property alias cfg_showBadge: badgeBox.checked
    property bool cfg_showBadgeDefault: true

    KIconThemes.IconDialog {
        id: picker
        onIconNameChanged: name => iconField.text = name || root.cfg_iconNameDefault
    }

    Kirigami.FormLayout {
        anchors.left: parent.left
        anchors.right: parent.right

        RowLayout {
            Kirigami.FormData.label: i18n("Icon:")
            spacing: Kirigami.Units.smallSpacing

            QQC2.Button {
                icon.name: iconField.text
                text: i18n("Choose…")
                onClicked: picker.open()
            }

            QQC2.TextField {
                id: iconField
                Layout.minimumWidth: Kirigami.Units.gridUnit * 12
                placeholderText: i18n("Icon name or path")
            }
        }

        QQC2.Label {
            text: i18n("A name from the icon theme (notifications, for example) or the path to an image file.")
            wrapMode: Text.WordWrap
            Layout.maximumWidth: Kirigami.Units.gridUnit * 22
            opacity: 0.7
            font: Kirigami.Theme.smallFont
        }

        QQC2.CheckBox {
            id: badgeBox
            Kirigami.FormData.label: i18n("History:")
            text: i18n("Mark the icon while there is something in the history")
        }
    }
}
