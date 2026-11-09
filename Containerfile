# Example downstream image integration. Build/test in a disposable Bazzite VM first.
ARG BASE_IMAGE=ghcr.io/ublue-os/bazzite:stable
FROM ${BASE_IMAGE}

USER root
COPY . /tmp/multipak-source
RUN python3 -m venv --system-site-packages /usr/libexec/multipak \
    && /usr/libexec/multipak/bin/python -m pip install --no-cache-dir /tmp/multipak-source \
    && ln -s /usr/libexec/multipak/bin/multipak-installer /usr/bin/multipak-installer \
    && ln -s /usr/libexec/multipak/bin/multipak-manager /usr/bin/multipak-manager \
    && install -Dm644 /tmp/multipak-source/data/io.github.multipak.installer.desktop \
       /usr/share/applications/io.github.multipak.installer.desktop \
    && install -Dm644 /tmp/multipak-source/data/io.github.multipak.manager.desktop \
       /usr/share/applications/io.github.multipak.manager.desktop \
    && install -Dm644 /tmp/multipak-source/data/io.github.multipak.packages.xml \
       /usr/share/mime/packages/io.github.multipak.packages.xml \
    && install -Dm644 /tmp/multipak-source/src/multipak/assets/multipak-appimage.xml \
       /usr/share/mime/packages/io.github.multipak.appimage.xml \
    && install -Dm644 /tmp/multipak-source/src/multipak/kirigami/assets/multipak-installer.svg \
       /usr/share/icons/hicolor/scalable/apps/io.github.multipak.installer.svg \
    && install -Dm644 /tmp/multipak-source/src/multipak/kirigami/assets/multipak-manager.svg \
       /usr/share/icons/hicolor/scalable/apps/io.github.multipak.manager.svg \
    && install -Dm644 /tmp/multipak-source/src/multipak/kirigami/assets/multipak-installer.png \
       /usr/share/icons/hicolor/256x256/apps/io.github.multipak.installer.png \
    && install -Dm644 /tmp/multipak-source/src/multipak/kirigami/assets/multipak-manager.png \
       /usr/share/icons/hicolor/256x256/apps/io.github.multipak.manager.png \
    && update-mime-database /usr/share/mime \
    && update-desktop-database /usr/share/applications \
    && /usr/libexec/multipak/bin/python -m multipak.integration \
       --system-file /usr/share/applications/mimeapps.list \
    && rm -rf /tmp/multipak-source
