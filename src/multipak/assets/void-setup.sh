#!/bin/sh
# MultiPak: install and update in a Void Linux environment (run with sudo).
#
#   --install FILE PKGVER ARCH  install the selected .xbps through a short-lived local
#                               repository, with dependencies from the configured ones;
#   --upgrade                   update the whole environment.
#
# XBPS refuses every transaction while a newer xbps is available, so xbps is updated
# first. Distrobox's own upgrade (xbps-install -Syu, without -y) also asks for
# confirmation, and without a terminal it aborts and still exits 0.
set -eu

update_xbps() {
	xbps-install -Suy xbps
}

# Void ships translations with each application and Distrobox prepares the glibc
# locales. Programs built with GTK 3 fall back to light Adwaita, while the host may
# send them an icon theme made for dark backgrounds, which makes toolbar icons
# invisible: follow the host user's dark preference.
gtk_preference() {
	owner=${SUDO_UID:-$(stat -c %u "$0" 2> /dev/null)}
	[ -n "$owner" ] || return 0
	dark=false
	for settings in /run/host/home/*/.config/gtk-3.0/settings.ini \
		/run/host/var/home/*/.config/gtk-3.0/settings.ini; do
		[ "$(stat -c %u "$settings" 2> /dev/null)" = "$owner" ] || continue
		if grep -qsi -e '^gtk-application-prefer-dark-theme *= *\(true\|1\)' \
			-e '^gtk-icon-theme-name *=.*dark' -e '^gtk-theme-name *=.*dark' "$settings"; then
			dark=true
		fi
	done
	mkdir -p /etc/xdg/gtk-3.0
	printf '[Settings]\ngtk-application-prefer-dark-theme=%s\n' "$dark" \
		> /etc/xdg/gtk-3.0/settings.ini
}

case "${1:-}" in
	--install)
		[ "$#" = 4 ] || { echo "usage: $0 --install FILE PKGVER ARCH" >&2; exit 2; }
		package=$2
		pkgver=$3
		architecture=$4
		update_xbps
		# Not essential: the application still installs without the setting.
		gtk_preference || echo "MultiPak: the GTK setting could not be written." >&2
		repo=$(mktemp -d "$(dirname -- "$package")/repo.XXXXXX")
		trap 'rm -rf -- "$repo"' EXIT
		trap 'exit 1' HUP INT TERM
		# xbps-rindex finds packages by their canonical file name.
		cp -- "$package" "$repo/$pkgver.$architecture.xbps"
		xbps-rindex -a "$repo/$pkgver.$architecture.xbps"
		# -R puts the local repository first and keeps the configured signed repositories
		# for dependencies. Local repositories are not signed. A private cache avoids
		# reusing another archive with exactly the same name and version.
		mkdir "$repo/cache"
		xbps-install -Sy -R "$repo" -c "$repo/cache" -- "$pkgver"
		;;
	--upgrade)
		update_xbps
		xbps-install -uy
		;;
	*)
		echo "usage: $0 --install FILE PKGVER ARCH | --upgrade" >&2
		exit 2
		;;
esac
