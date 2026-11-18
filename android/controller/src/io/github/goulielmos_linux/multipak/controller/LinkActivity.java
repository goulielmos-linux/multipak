// SPDX-License-Identifier: GPL-3.0-only
package io.github.goulielmos_linux.multipak.controller;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.util.Log;
import java.util.List;
import java.util.Locale;

/**
 * Default Android browser that hands links to the Linux desktop.
 *
 * Each link becomes one small JSON file in files/outbox. Only the MultiPak service
 * on the host reads that private folder; this app has no network permission.
 * Login tabs (Custom Tabs) stay in an Android browser so the app gets its result.
 * While the desktop session has not turned links on, everything stays in Android,
 * and so do the links of the apps listed in multipak_links_android.
 */
public final class LinkActivity extends Activity {
    private static final String TAG = "MultiPakLinks";
    private static final String SESSION = "android.support.customtabs.extra.SESSION";
    private static final String ENABLED = "multipak_links";
    private static final String IN_ANDROID = "multipak_links_android";
    private static final String PREFERRED = "org.lineageos.jelly";
    private static final int MAX_URI = 4096;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        try {
            handle(getIntent());
        } catch (RuntimeException error) {
            Log.w(TAG, "Could not open the link", error);
        }
        finish();
    }

    private void handle(Intent intent) {
        Uri uri = intent == null ? null : intent.getData();
        String scheme = uri == null || uri.getScheme() == null
                ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        boolean web = scheme.equals("http") || scheme.equals("https");
        if (!web && !scheme.equals("mailto")) {
            return;
        }
        if (!linksOnLinux() || fromAndroidOnlyApp()) {
            forwardInAndroid(intent);
        } else if (!(web && isLoginTab(intent) && forwardInAndroid(intent))) {
            send(web ? uri.toString() : mailto(intent, uri));
        }
    }

    private boolean linksOnLinux() {
        try {
            return "1".equals(Settings.Secure.getString(getContentResolver(), ENABLED));
        } catch (RuntimeException unreadable) {
            return false;
        }
    }

    /**
     * Apps the user chose to keep their links in Android. The referrer is the calling
     * app unless that app names another one itself; either way, only that app's own
     * links stay in Android, so nothing more trusted depends on it.
     */
    private boolean fromAndroidOnlyApp() {
        try {
            Uri referrer = getReferrer();
            String caller = referrer == null || !"android-app".equals(referrer.getScheme())
                    ? null : referrer.getHost();
            String list = Settings.Secure.getString(getContentResolver(), IN_ANDROID);
            if (caller == null || list == null) {
                return false;
            }
            for (String item : list.split(",")) {
                if (item.equals(caller)) {
                    return true;
                }
            }
        } catch (RuntimeException unreadable) {
            // Without the list, links follow the general choice.
        }
        return false;
    }

    /** Custom Tabs always carry the session extra, even when it is null. */
    private static boolean isLoginTab(Intent intent) {
        try {
            return intent.hasExtra(SESSION);
        } catch (RuntimeException unreadable) {
            return false;
        }
    }

    /** Hand the same request to another Android app, preferring the system browser. */
    private boolean forwardInAndroid(Intent original) {
        Intent probe = new Intent(original);
        probe.setComponent(null);
        probe.setPackage(null);
        List<ResolveInfo> apps = getPackageManager().queryIntentActivities(probe, PackageManager.MATCH_ALL);
        ActivityInfo target = null;
        for (ResolveInfo info : apps) {
            ActivityInfo activity = info.activityInfo;
            if (activity == null || activity.packageName.equals(getPackageName())) {
                continue;
            }
            if (target == null || activity.packageName.equals(PREFERRED)) {
                target = activity;
            }
        }
        if (target == null) {
            return false;
        }
        Intent forward = new Intent(original);
        forward.setComponent(new ComponentName(target.packageName, target.name));
        try {
            startActivity(forward);
            return true;
        } catch (ActivityNotFoundException | SecurityException error) {
            Log.w(TAG, "Android app unavailable", error);
            return false;
        }
    }

    /** Apps often put the subject and body in extras; keep them in the address for Linux. */
    private static String mailto(Intent intent, Uri uri) {
        StringBuilder result = new StringBuilder(uri.toString());
        boolean query = uri.toString().contains("?");
        try {
            String[][] fields = {
                {"to", join(intent.getStringArrayExtra(Intent.EXTRA_EMAIL))},
                {"cc", join(intent.getStringArrayExtra(Intent.EXTRA_CC))},
                {"bcc", join(intent.getStringArrayExtra(Intent.EXTRA_BCC))},
                {"subject", intent.getStringExtra(Intent.EXTRA_SUBJECT)},
                {"body", text(intent.getCharSequenceExtra(Intent.EXTRA_TEXT))},
            };
            for (String[] field : fields) {
                if (field[1] == null || field[1].isEmpty()) {
                    continue;
                }
                result.append(query ? '&' : '?').append(field[0]).append('=').append(Uri.encode(field[1]));
                query = true;
            }
        } catch (RuntimeException unreadable) {
            // The address alone is still useful.
        }
        return result.toString();
    }

    private static String join(String[] values) {
        return values == null ? null : String.join(",", values);
    }

    private static String text(CharSequence value) {
        return value == null ? null : value.toString();
    }

    private void send(String uri) {
        if (uri.length() > MAX_URI) {
            Log.w(TAG, "Link too long for the desktop");
            return;
        }
        Outbox.write(this, "{\"v\":1,\"uri\":" + Outbox.quote(uri) + "}");
    }
}
