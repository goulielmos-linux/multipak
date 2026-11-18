// SPDX-License-Identifier: GPL-3.0-only
package io.github.goulielmos_linux.multipak.controller;

import android.content.ComponentName;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Handler;
import android.os.Looper;
import android.service.notification.NotificationListenerService;
import android.util.Log;
import java.util.List;

/**
 * Follows the active Android media session and leaves its state in the outbox, so the
 * desktop media controls (MPRIS) show it. Being a notification listener is only what
 * lets Android share its media sessions; notifications themselves are never read.
 * The desktop sends the buttons back as media keys, not through this service.
 */
public final class MediaListener extends NotificationListenerService
        implements MediaSessionManager.OnActiveSessionsChangedListener {
    private static final String TAG = "MultiPakMedia";
    private static final int MAX_TEXT = 256;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private MediaSessionManager sessions;
    private MediaController current;
    private String last;

    private final MediaController.Callback callback = new MediaController.Callback() {
        @Override
        public void onPlaybackStateChanged(PlaybackState state) {
            publish();
        }

        @Override
        public void onMetadataChanged(MediaMetadata metadata) {
            publish();
        }

        @Override
        public void onSessionDestroyed() {
            follow(null);
        }
    };

    @Override
    public void onListenerConnected() {
        last = null; // A new Android start: the desktop needs the state again.
        sessions = getSystemService(MediaSessionManager.class);
        ComponentName self = new ComponentName(this, MediaListener.class);
        try {
            sessions.addOnActiveSessionsChangedListener(this, self, handler);
            onActiveSessionsChanged(sessions.getActiveSessions(self));
        } catch (SecurityException error) {
            Log.w(TAG, "Android did not share its media sessions", error);
        }
    }

    @Override
    public void onListenerDisconnected() {
        if (sessions != null) {
            sessions.removeOnActiveSessionsChangedListener(this);
        }
        follow(null);
    }

    /** Android lists the most recent session first; one that is playing wins. */
    @Override
    public void onActiveSessionsChanged(List<MediaController> controllers) {
        MediaController chosen = null;
        if (controllers != null) {
            for (MediaController controller : controllers) {
                if (chosen == null || playing(controller) && !playing(chosen)) {
                    chosen = controller;
                }
            }
        }
        follow(chosen);
    }

    private void follow(MediaController controller) {
        boolean same = current != null && controller != null
                && current.getSessionToken().equals(controller.getSessionToken());
        if (!same) {
            if (current != null) {
                current.unregisterCallback(callback);
            }
            current = controller;
            if (current != null) {
                current.registerCallback(callback, handler);
            }
        }
        publish();
    }

    private void publish() {
        String json;
        try {
            json = "{\"v\":1,\"media\":" + describe(current) + "}";
        } catch (RuntimeException unreadable) {
            Log.w(TAG, "Could not read the media session", unreadable);
            return;
        }
        if (!json.equals(last) && Outbox.write(this, json)) {
            last = json;
        }
    }

    /** {"package","state","title","artist"}, or {} when nothing is playing. */
    private static String describe(MediaController controller) {
        if (controller == null) {
            return "{}";
        }
        String state = state(controller.getPlaybackState());
        MediaMetadata metadata = controller.getMetadata();
        String title = text(metadata, MediaMetadata.METADATA_KEY_TITLE,
                MediaMetadata.METADATA_KEY_DISPLAY_TITLE);
        String artist = text(metadata, MediaMetadata.METADATA_KEY_ARTIST,
                MediaMetadata.METADATA_KEY_ALBUM_ARTIST, MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE);
        if (state.equals("stopped") && title == null) {
            return "{}";
        }
        StringBuilder result = new StringBuilder("{\"package\":")
                .append(Outbox.quote(controller.getPackageName()))
                .append(",\"state\":\"").append(state).append('"');
        if (title != null) {
            result.append(",\"title\":").append(Outbox.quote(title));
        }
        if (artist != null) {
            result.append(",\"artist\":").append(Outbox.quote(artist));
        }
        return result.append('}').toString();
    }

    private static boolean playing(MediaController controller) {
        return state(controller.getPlaybackState()).equals("playing");
    }

    private static String state(PlaybackState playback) {
        if (playback == null) {
            return "stopped";
        }
        switch (playback.getState()) {
            case PlaybackState.STATE_PLAYING:
            case PlaybackState.STATE_BUFFERING:
            case PlaybackState.STATE_CONNECTING:
            case PlaybackState.STATE_FAST_FORWARDING:
            case PlaybackState.STATE_REWINDING:
            case PlaybackState.STATE_SKIPPING_TO_NEXT:
            case PlaybackState.STATE_SKIPPING_TO_PREVIOUS:
            case PlaybackState.STATE_SKIPPING_TO_QUEUE_ITEM:
                return "playing";
            case PlaybackState.STATE_PAUSED:
                return "paused";
            default:
                return "stopped";
        }
    }

    private static String text(MediaMetadata metadata, String... keys) {
        if (metadata == null) {
            return null;
        }
        for (String key : keys) {
            String value = clean(metadata.getText(key));
            if (value != null) {
                return value;
            }
        }
        return null;
    }

    /** Control characters become spaces; at most MAX_TEXT, never cutting an emoji in half. */
    private static String clean(CharSequence value) {
        if (value == null) {
            return null;
        }
        String text = value.toString();
        StringBuilder result = new StringBuilder();
        for (int i = 0; i < text.length(); ) {
            int point = text.codePointAt(i);
            i += Character.charCount(point);
            int type = Character.getType(point);
            if (type == Character.SURROGATE) {
                continue; // Half of a broken pair.
            }
            if (type == Character.CONTROL) {
                point = ' ';
            }
            if (result.length() + Character.charCount(point) > MAX_TEXT) {
                break;
            }
            result.appendCodePoint(point);
        }
        String trimmed = result.toString().trim();
        return trimmed.isEmpty() ? null : trimmed;
    }
}
