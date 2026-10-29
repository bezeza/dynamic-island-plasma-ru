import QtQuick
import QtQuick.Window

import org.kde.layershell as LayerShell

import DynamicIsland

/*
 * A transparent, screen-wide layer-shell surface pinned to the top edge.
 *
 * It stays on the overlay layer so the island can sit above a maximised window
 * or the panel, and it claims no exclusive zone — nothing on the desktop is
 * pushed out of the way.  Clicks only land on the island itself: the input
 * region is narrowed to the pill in Island.syncInputRegion().
 */
Window {
    id: root

    visible: island.surfaceNeeded
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint

    // Enough room for the tallest island plus its bloom.
    width: Screen.width
    height: Math.min(Screen.height,
                     Cfg.values.maxHeight + Cfg.values.topMargin + 120)

    LayerShell.Window.scope: "dynamic-island"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop
                               | LayerShell.Window.AnchorLeft
                               | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.wantsToBeOnActiveScreen: Cfg.values.screen === "active"
    LayerShell.Window.keyboardInteractivity: island.wantsKeyboard
                                             ? LayerShell.Window.KeyboardInteractivityOnDemand
                                             : LayerShell.Window.KeyboardInteractivityNone

    /* The palette is a setting, and Theme is a singleton — which has no
     * business reaching into the application's context to read one. So it is
     * handed down from here, where Cfg is in scope. */
    Binding {
        target: Theme
        property: "mode"
        value: Cfg.values.theme || "system"
    }

    Island {
        id: island
        anchors.horizontalCenter: parent.horizontalCenter
        y: Cfg.values.topMargin + dragOffset
    }
}
