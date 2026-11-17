#!/bin/sh
# MultiPak: prepare an Alpine Linux environment before installing an application.
#
# Distrobox already knows apk and installs what it needs. This script, run with sudo
# before each installation, adds what desktop programs miss in Alpine's minimal image:
#   - translations: Alpine splits them into name-lang packages, installed automatically
#     with each program once the "lang" package is present; musl-locales translates the
#     C library's own messages;
#   - a GTK 3 setting that follows the host's dark preference (see gtk_preference).
set -u

# Programs built with GTK 3 fall back to light Adwaita, while the host may send them an
# icon theme made for dark backgrounds, which makes the toolbar icons invisible.
# Follow the host user's dark preference.
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
	--prepare)
		gtk_preference
		# Not essential: the application still installs if they are unavailable.
		apk add --no-progress lang musl-locales ||
			echo "MultiPak: translations could not be added." >&2
		;;
	*)
		echo "usage: $0 --prepare" >&2
		exit 2
		;;
esac
