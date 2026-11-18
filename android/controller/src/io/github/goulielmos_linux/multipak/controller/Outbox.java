// SPDX-License-Identifier: GPL-3.0-only
package io.github.goulielmos_linux.multipak.controller;

import android.content.Context;
import android.util.Log;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Locale;

/**
 * Private folder files/outbox, read only by the MultiPak service on the host.
 * Each request is one small JSON file, renamed in when complete.
 */
final class Outbox {
    private static final String TAG = "MultiPakOutbox";
    private static final int MAX_PENDING = 16;
    private static final SecureRandom RANDOM = new SecureRandom();

    private Outbox() {
    }

    static boolean write(Context context, String json) {
        File outbox = new File(context.getFilesDir(), "outbox");
        if (!outbox.isDirectory() && !outbox.mkdirs()) {
            Log.w(TAG, "Could not create the outbox");
            return false;
        }
        String[] pending = outbox.list();
        if (pending != null && pending.length >= MAX_PENDING) {
            Log.w(TAG, "The desktop is not reading the outbox; dropping a request");
            return false;
        }
        String name = System.currentTimeMillis() + "-" + Long.toHexString(RANDOM.nextLong());
        File temporary = new File(context.getFilesDir(), name + ".tmp");
        byte[] data = json.getBytes(StandardCharsets.UTF_8);
        try (FileOutputStream output = new FileOutputStream(temporary)) {
            output.write(data);
            output.getFD().sync();
        } catch (java.io.IOException error) {
            temporary.delete();
            Log.w(TAG, "Could not write a request", error);
            return false;
        }
        // Rename so the host never sees a partly written request.
        if (!temporary.renameTo(new File(outbox, name + ".json"))) {
            temporary.delete();
            Log.w(TAG, "Could not queue a request");
            return false;
        }
        return true;
    }

    /** JSON string with everything outside printable ASCII escaped, as the host reads ASCII. */
    static String quote(String value) {
        StringBuilder result = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '"' || c == '\\') {
                result.append('\\').append(c);
            } else if (c < 0x20 || c > 0x7e) {
                result.append(String.format(Locale.ROOT, "\\u%04x", (int) c));
            } else {
                result.append(c);
            }
        }
        return result.append('"').toString();
    }
}
