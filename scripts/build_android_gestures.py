"""Build the small Android accessibility helper with Android SDK 33+ and JDK 17.

Pass --java-home, --build-tools, --android-jar and a persistent --keystore.
The signing key is kept outside the repository; use the same key for upgrades.
The keystore password is read from MULTIPAK_KEYSTORE_PASSWORD.
"""

import argparse
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("java-home", "build-tools", "android-jar", "keystore"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "android/gestures"
    output = root / "vendor/waydroid/assets/multipak-gestures.apk"
    output.parent.mkdir(exist_ok=True)
    env = {**os.environ, "JAVA_HOME": str(args.java_home)}
    env["PATH"] = str(args.java_home / "bin") + os.pathsep + env["PATH"]
    if not env.get("MULTIPAK_KEYSTORE_PASSWORD"):
        raise SystemExit("Set MULTIPAK_KEYSTORE_PASSWORD for the existing signing key")

    def run(*command):
        subprocess.run([str(part) for part in command], check=True, env=env)

    with tempfile.TemporaryDirectory(prefix="multipak-gestures-") as temp:
        build = Path(temp)
        classes = build / "classes"
        classes.mkdir()
        run(args.build_tools / "aapt2", "compile", "--dir", source / "res", "-o", build / "res.zip")
        run(
            args.build_tools / "aapt2",
            "link",
            "-I",
            args.android_jar,
            "--manifest",
            source / "AndroidManifest.xml",
            "-o",
            build / "unsigned.apk",
            build / "res.zip",
        )
        run(
            args.java_home / "bin/javac",
            "--release",
            "8",
            "-classpath",
            args.android_jar,
            "-d",
            classes,
            *sorted((source / "src").rglob("*.java")),
        )
        run(
            args.build_tools / "d8",
            "--min-api",
            "26",
            "--lib",
            args.android_jar,
            "--output",
            build,
            *sorted(classes.rglob("*.class")),
        )
        with zipfile.ZipFile(build / "unsigned.apk", "a", zipfile.ZIP_DEFLATED) as archive:
            archive.write(build / "classes.dex", "classes.dex")
        run(args.build_tools / "zipalign", "-f", "4", build / "unsigned.apk", build / "aligned.apk")
        run(
            args.build_tools / "apksigner",
            "sign",
            "--ks",
            args.keystore,
            "--ks-pass",
            "env:MULTIPAK_KEYSTORE_PASSWORD",
            "--out",
            output,
            build / "aligned.apk",
        )
        run(args.build_tools / "apksigner", "verify", output)
    print(output)


if __name__ == "__main__":
    main()
