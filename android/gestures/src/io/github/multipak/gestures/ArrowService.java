// SPDX-License-Identifier: GPL-3.0-only
package io.github.multipak.gestures;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.database.ContentObserver;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Handler;
import android.provider.Settings;
import android.util.Log;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** Local to this Android instance: no host keyboard access or network permission. */
public final class ArrowService extends AccessibilityService {
    private static final String PROFILES = "multipak_arrow_packages";
    private final Handler handler = new Handler();
    private final Set<String> enabled = new HashSet<>();
    private final Set<Integer> pressed = new HashSet<>();
    private final ArrayDeque<Swipe> pending = new ArrayDeque<>();
    private boolean busy;
    private ContentObserver observer;

    private static final class Target {
        final String name;
        final int window;
        final Rect bounds;
        Target(String name, int window, Rect bounds) {
            this.name = name; this.window = window; this.bounds = bounds;
        }
    }
    private static final class Swipe {
        final Target target;
        final int key;
        Swipe(Target target, int key) { this.target = target; this.key = key; }
    }

    @Override public void onServiceConnected() {
        observer = new ContentObserver(handler) {
            @Override public void onChange(boolean self) { reload(); }
        };
        getContentResolver().registerContentObserver(Settings.Secure.getUriFor(PROFILES), false, observer);
        reload();
        Log.i("MultiPakGestures", "Arrow service ready");
    }
    private void reload() {
        enabled.clear();
        String value = Settings.Secure.getString(getContentResolver(), PROFILES);
        if (value != null && !value.isEmpty()) enabled.addAll(Arrays.asList(value.split(",")));
        pending.clear();
    }
    private Target target() {
        for (AccessibilityWindowInfo window : getWindows()) {
            if (!window.isFocused() || window.getType() != AccessibilityWindowInfo.TYPE_APPLICATION) continue;
            AccessibilityNodeInfo root = window.getRoot();
            if (root == null) return null;
            try {
                CharSequence pkg = root.getPackageName();
                if (pkg == null || !enabled.contains(pkg.toString())) return null;
                AccessibilityNodeInfo input = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                if (input != null) {
                    boolean editing = input.isEditable();
                    input.recycle();
                    if (editing) return null;
                }
                Rect bounds = new Rect();
                window.getBoundsInScreen(bounds);
                if (bounds.width() < 80 || bounds.height() < 80) return null;
                return new Target(pkg.toString(), window.getId(), bounds);
            } finally { root.recycle(); }
        }
        return null;
    }
    @Override protected boolean onKeyEvent(KeyEvent event) {
        int key = event.getKeyCode();
        if (key < KeyEvent.KEYCODE_DPAD_UP || key > KeyEvent.KEYCODE_DPAD_RIGHT) return false;
        if (event.getAction() == KeyEvent.ACTION_UP) return pressed.remove(key);
        if (event.getAction() != KeyEvent.ACTION_DOWN) return false;
        if (pressed.contains(key)) return true; // Never repeat a swipe while a key is held.
        if (event.getRepeatCount() != 0 || event.isAltPressed() || event.isCtrlPressed()
                || event.isMetaPressed() || event.isShiftPressed()) return false;
        Target target = target();
        if (target == null) return false;
        pressed.add(key);
        if (pending.size() < 2) pending.add(new Swipe(target, key));
        next();
        return true;
    }
    private void next() {
        if (busy || pending.isEmpty()) return;
        Swipe swipe = pending.remove();
        Target current = target();
        if (current == null || current.window != swipe.target.window
                || !current.name.equals(swipe.target.name)) {
            pending.clear(); return;
        }
        Rect b = current.bounds;
        float x = b.exactCenterX(), y = b.exactCenterY();
        float distance = Math.min(b.width(), b.height()) * 0.28f;
        float dx = swipe.key == KeyEvent.KEYCODE_DPAD_LEFT ? -distance
            : swipe.key == KeyEvent.KEYCODE_DPAD_RIGHT ? distance : 0;
        float dy = swipe.key == KeyEvent.KEYCODE_DPAD_UP ? -distance
            : swipe.key == KeyEvent.KEYCODE_DPAD_DOWN ? distance : 0;
        Path path = new Path(); path.moveTo(x, y); path.lineTo(x + dx, y + dy);
        GestureDescription gesture = new GestureDescription.Builder()
            .addStroke(new GestureDescription.StrokeDescription(path, 0, 110)).build();
        busy = true;
        boolean accepted = dispatchGesture(gesture, new GestureResultCallback() {
            @Override public void onCompleted(GestureDescription g) {
                busy = false; next();
            }
            @Override public void onCancelled(GestureDescription g) {
                busy = false; pending.clear();
            }
        }, handler);
        if (!accepted) { busy = false; pending.clear(); }
    }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        // Recheck window identity immediately before each queued gesture.
        if (target() == null) pending.clear();
    }
    @Override public void onInterrupt() { pending.clear(); pressed.clear(); }
    @Override public void onDestroy() {
        if (observer != null) getContentResolver().unregisterContentObserver(observer);
        pending.clear(); pressed.clear();
        super.onDestroy();
    }
}
