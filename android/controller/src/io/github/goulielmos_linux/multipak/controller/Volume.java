// SPDX-License-Identifier: GPL-3.0-only
package io.github.goulielmos_linux.multipak.controller;

import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioManager;
import android.media.AudioPlaybackConfiguration;
import android.os.Handler;
import android.os.Looper;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/**
 * Volume of each Android app. Not part of the app: the MultiPak service on the host runs
 * this class as its own process ("app_process" with this APK as the class path), because
 * only Android's own users may see and change the players of other apps.
 *
 * Output: "ready", then "playing [package]..." each time the apps that play change.
 * Input: "volume <package> <0-100>", the app's share of its own loudness.
 */
public final class Volume {
    private static final int STARTED = 2; // AudioPlaybackConfiguration.PLAYER_STATE_STARTED
    private static final Map<String, Float> VOLUMES = new HashMap<>(); // Apps below 100.
    private static AudioManager audio;
    private static PackageManager packages;
    private static String shown = "";

    private Volume() {
    }

    public static void main(String[] arguments) throws Exception {
        Looper.prepareMainLooper();
        Class<?> thread = Class.forName("android.app.ActivityThread");
        Context context = (Context) thread.getMethod("getSystemContext")
                .invoke(thread.getMethod("systemMain").invoke(null));
        audio = context.getSystemService(AudioManager.class);
        packages = context.getPackageManager();
        Handler handler = new Handler(Looper.getMainLooper());
        audio.registerAudioPlaybackCallback(new AudioManager.AudioPlaybackCallback() {
            @Override
            public void onPlaybackConfigChanged(List<AudioPlaybackConfiguration> players) {
                update(players, null);
            }
        }, handler);
        System.out.println("ready");
        update(audio.getActivePlaybackConfigurations(), null);
        new Thread(() -> {
            try (BufferedReader input = new BufferedReader(new InputStreamReader(System.in))) {
                for (String line = input.readLine(); line != null; line = input.readLine()) {
                    String[] words = line.split(" ");
                    handler.post(() -> command(words));
                }
            } catch (Exception error) {
                // The host closed the pipe.
            }
            System.exit(0);
        }).start();
        Looper.loop();
    }

    private static void command(String[] words) {
        try {
            int percent = Integer.parseInt(words[2]);
            if (words.length != 3 || !words[0].equals("volume") || percent < 0 || percent > 100) {
                return;
            }
            // The same curve as the desktop's mixer: half the slider is not half the signal.
            float volume = percent / 100f;
            volume = volume * volume * volume;
            if (percent == 100) {
                VOLUMES.remove(words[1]);
            } else {
                VOLUMES.put(words[1], volume);
            }
            update(audio.getActivePlaybackConfigurations(), words[1]);
        } catch (RuntimeException error) {
            // Not a command.
        }
    }

    /** Sets the volume of the players of the apps in VOLUMES, and of "changed". */
    private static void update(List<AudioPlaybackConfiguration> players, String changed) {
        TreeSet<String> playing = new TreeSet<>();
        for (AudioPlaybackConfiguration player : players) {
            try {
                String[] names = packages.getPackagesForUid((int) call(player, "getClientUid"));
                if (names == null || names.length != 1) {
                    continue; // Apps that share an Android user cannot be told apart.
                }
                if ((int) call(player, "getPlayerState") == STARTED) {
                    playing.add(names[0]);
                }
                Float volume = VOLUMES.get(names[0]);
                if (volume != null || names[0].equals(changed)) {
                    Object proxy = call(player, "getPlayerProxy");
                    proxy.getClass().getMethod("setVolume", float.class)
                            .invoke(proxy, volume == null ? 1f : volume);
                }
            } catch (Exception error) {
                // A player that went away meanwhile.
            }
        }
        String line = "playing" + (playing.isEmpty() ? "" : " " + String.join(" ", playing));
        if (!line.equals(shown)) {
            shown = line;
            System.out.println(line);
        }
    }

    private static Object call(Object player, String method) throws Exception {
        return player.getClass().getMethod(method).invoke(player); // Hidden from apps.
    }
}
