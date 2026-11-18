// SPDX-License-Identifier: GPL-3.0-only
package io.github.goulielmos_linux.multipak.controller;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.util.Log;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;

/**
 * "Linux" in Android's share menu: each shared file is copied to the private folder
 * files/sent, renamed in when complete, and the MultiPak service on the host moves it to
 * the Linux Downloads folder. Only content:// addresses are read, so another app cannot
 * make this one send its own private files.
 */
public final class ShareActivity extends Activity {
    private static final String TAG = "MultiPakShare";
    private static final int MAX_PENDING = 16;
    private static final int MAX_NAME_BYTES = 200;
    private static final long OLD_PART_MS = 24L * 3600 * 1000;
    private static final SecureRandom RANDOM = new SecureRandom();

    private static final class Item {
        final String name;
        final InputStream input;

        Item(String name, InputStream input) {
            this.name = name;
            this.input = input;
        }
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        final List<Item> items = new ArrayList<>();
        try {
            collect(getIntent(), items);
        } catch (RuntimeException error) {
            Log.w(TAG, "Could not read what was shared", error);
        }
        // The files are open already: the permission to read them ends with this activity.
        finish();
        if (!items.isEmpty()) {
            final Context context = getApplicationContext();
            new Thread(() -> copy(context, items)).start();
        }
    }

    private void collect(Intent intent, List<Item> items) {
        if (intent == null) {
            return;
        }
        List<Uri> uris = new ArrayList<>();
        if (Intent.ACTION_SEND.equals(intent.getAction())) {
            Uri uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (uri != null) {
                uris.add(uri);
            }
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
            ArrayList<Uri> list = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (list != null) {
                uris.addAll(list);
            }
        }
        ContentResolver resolver = getContentResolver();
        for (Uri uri : uris) {
            if (items.size() >= MAX_PENDING) {
                break;
            }
            if (uri == null || !ContentResolver.SCHEME_CONTENT.equals(uri.getScheme())) {
                Log.w(TAG, "Only shared content is sent to Linux");
                continue;
            }
            try {
                ParcelFileDescriptor file = resolver.openFileDescriptor(uri, "r");
                if (file != null) {
                    items.add(new Item(name(resolver, uri), new ParcelFileDescriptor.AutoCloseInputStream(file)));
                }
            } catch (IOException | RuntimeException error) {
                Log.w(TAG, "Could not open a shared file", error);
            }
        }
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (uris.isEmpty() && text != null && text.length() > 0) {
            byte[] data = text.toString().getBytes(StandardCharsets.UTF_8);
            items.add(new Item("Android.txt", new ByteArrayInputStream(data)));
        }
    }

    private static String name(ContentResolver resolver, Uri uri) {
        String name = null;
        try (Cursor cursor = resolver.query(uri, new String[] {OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst() && !cursor.isNull(0)) {
                name = cursor.getString(0);
            }
        } catch (RuntimeException error) {
            Log.w(TAG, "Could not read a shared file's name", error);
        }
        if (name == null) {
            name = uri.getLastPathSegment();
        }
        return clean(name);
    }

    /** A plain file name: no folders or control characters, short enough for the prefix. */
    static String clean(String name) {
        StringBuilder result = new StringBuilder();
        if (name != null) {
            for (int i = 0; i < name.length(); i++) {
                char c = name.charAt(i);
                result.append(c == '/' || c < 0x20 || c == 0x7f || Character.isSurrogate(c) ? '_' : c);
            }
        }
        String plain = result.toString().trim();
        if (plain.isEmpty() || plain.equals(".") || plain.equals("..")) {
            plain = "Android";
        }
        int dot = plain.lastIndexOf('.');
        String extension = dot > 0 && plain.length() - dot <= 16 ? plain.substring(dot) : "";
        String stem = plain.substring(0, plain.length() - extension.length());
        while (!stem.isEmpty() && (stem + extension).getBytes(StandardCharsets.UTF_8).length > MAX_NAME_BYTES) {
            stem = stem.substring(0, stem.length() - 1);
        }
        return stem.isEmpty() ? "Android" + extension : stem + extension;
    }

    private static void copy(Context context, List<Item> items) {
        File sent = new File(context.getFilesDir(), "sent");
        if (!sent.isDirectory() && !sent.mkdirs()) {
            Log.w(TAG, "Could not create the folder for Linux");
            return;
        }
        removeOldParts(context.getFilesDir());
        for (Item item : items) {
            String[] pending = sent.list();
            if (pending != null && pending.length >= MAX_PENDING) {
                Log.w(TAG, "The desktop is not reading the files; dropping " + item.name);
                close(item.input);
                continue;
            }
            String prefix = System.currentTimeMillis() + "-" + Long.toHexString(RANDOM.nextLong());
            File temporary = new File(context.getFilesDir(), prefix + ".part");
            try (InputStream input = item.input; FileOutputStream output = new FileOutputStream(temporary)) {
                byte[] buffer = new byte[1024 * 1024];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                }
                output.getFD().sync();
            } catch (IOException | RuntimeException error) {
                temporary.delete();
                Log.w(TAG, "Could not copy " + item.name, error);
                continue;
            }
            // Renamed in, so the host never sees a partly written file.
            if (!temporary.renameTo(new File(sent, prefix + "-" + item.name))) {
                temporary.delete();
                Log.w(TAG, "Could not queue " + item.name);
            }
        }
    }

    /** Copies cut short when Android stopped. */
    private static void removeOldParts(File folder) {
        File[] files = folder.listFiles();
        long now = System.currentTimeMillis();
        for (File file : files == null ? new File[0] : files) {
            if (file.getName().endsWith(".part") && now - file.lastModified() > OLD_PART_MS) {
                file.delete();
            }
        }
    }

    private static void close(InputStream input) {
        try {
            input.close();
        } catch (IOException ignored) {
            // Nothing else to do.
        }
    }
}
