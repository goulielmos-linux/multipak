#!/bin/sh
# MultiPak: prepare a Pisi Linux image before Distrobox sets it up.
# Distrobox does not know the pisi package manager, so this pre-init hook installs
# what Distrobox and MultiPak need and then marks the basic setup as done.
# With --upgrade, it updates the container instead, as distrobox upgrade cannot.
command -v pisi > /dev/null 2>&1 || exit 0
[ "$1" != --upgrade ] && [ -e /.containersetupdone ] && exit 0
set -e
# pisi runs package scripts through COMAR, which needs the system D-Bus. Distrobox
# does not share the host's, so start the container's own.
if ! { [ -S /run/dbus/system_bus_socket ] && [ -f /run/dbus/pid ] &&
	kill -0 "$(cat /run/dbus/pid)" 2> /dev/null; }; then
	# A socket left by an earlier start of the container refuses connections.
	rm -f /run/dbus/pid /run/dbus/system_bus_socket
	mkdir -p /run/dbus
	dbus-daemon --system --fork
fi
options="--no-color --yes-all --ignore-safety --ignore-file-conflicts"
if [ "$1" = --upgrade ]; then
	pisi update-repo --no-color
	# shellcheck disable=SC2086
	exec pisi upgrade $options
fi
if ! pisi list-repo 2> /dev/null | grep -q '^.*beta'; then
	pisi add-repo beta https://beta.pisilinux.org/pisi-index.xml.xz
fi
# Bring the image up to date first, as its own build does; the repository has dependency
# cycles (libutil-linux needs python3), so python3 is installed alone before the rest.
# shellcheck disable=SC2086
pisi upgrade $options --ignore-dependency
# shellcheck disable=SC2086
pisi install $options --ignore-dependency python3
# shellcheck disable=SC2086
pisi install $options python3 sudo util-linux shadow procps findutils diffutils \
	which less gnupg mesa vulkan xkeyboard-config dejavu-fonts glibc-locales-en
# Graphical programs need the host's language; GTK falls back to C without it.
locale=$(sed -n 's/^LANG=["'\'']*\([^"'\'']*\).*/\1/p' /run/host/etc/locale.conf \
	/run/host/etc/default/locale 2> /dev/null | head -n 1)
case "$locale" in
	[a-z][a-z]_[A-Z][A-Z].UTF-8 | [a-z][a-z][a-z]_[A-Z][A-Z].UTF-8)
		# shellcheck disable=SC2086
		pisi install $options "glibc-locales-${locale%%_*}" || true
		localedef -i "${locale%%.*}" -c -f UTF-8 "$locale" || true
		;;
esac
localedef -i en_US -c -f UTF-8 en_US.UTF-8 || true
# Pisi's sudoers does not read /etc/sudoers.d, where Distrobox grants the user sudo.
grep -q '^[@#]includedir /etc/sudoers.d' /etc/sudoers ||
	printf '\n@includedir /etc/sudoers.d\n' >> /etc/sudoers
touch /.containersetupdone
