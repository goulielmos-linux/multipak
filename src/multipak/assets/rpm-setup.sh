#!/bin/sh
# MultiPak: prepare a Fedora, Mageia, openSUSE or ALT Linux environment before installing an
# application. Run with sudo before each installation.
#
# Distrobox knows these package managers and installs what it needs, but their
# images have no Breeze GTK theme, and openSUSE's has no icon theme nor SVG loader either:
# applications then show broken toolbar icons and ignore the host's dark theme.
#   - adds an icon theme, the SVG loader and the Breeze GTK 2 and 3 themes, when the
#     distribution has them (not essential: the application installs without them);
#   - follows the host user's dark preference: GTK 3 through
#     gtk-application-prefer-dark-theme, GTK 2 by including Breeze or Breeze-Dark.
#     The host's XSETTINGS sends an empty theme name, so a GTK 2 theme name would be
#     ignored; an included gtkrc is not.
set -u

dark_preference() {
	owner=${SUDO_UID:-$(stat -c %u "$0" 2> /dev/null)}
	[ -n "$owner" ] || return 1
	for settings in /run/host/home/*/.config/gtk-3.0/settings.ini \
		/run/host/var/home/*/.config/gtk-3.0/settings.ini; do
		[ "$(stat -c %u "$settings" 2> /dev/null)" = "$owner" ] || continue
		if grep -qsi -e '^gtk-application-prefer-dark-theme *= *\(true\|1\)' \
			-e '^gtk-icon-theme-name *=.*dark' -e '^gtk-theme-name *=.*dark' "$settings"; then
			return 0
		fi
	done
	return 1
}

# Each package on its own: a name the distribution lacks does not stop the others.
add_packages() {
	for package in "$@"; do
		if command -v zypper > /dev/null; then
			zypper --non-interactive --quiet install "$package"
		elif command -v apt-get > /dev/null; then
			apt-get -y -q install "$package"
		else
			dnf -y -q install "$package"
		fi > /dev/null 2>&1 || echo "MultiPak: $package is not available." >&2
	done
}

desktop_packages() {
	if command -v zypper > /dev/null; then
		add_packages adwaita-icon-theme gdk-pixbuf-loader-rsvg \
			gtk2-metatheme-breeze6 gtk3-metatheme-breeze6
	elif command -v apt-get > /dev/null; then
		add_packages icon-theme-adwaita librsvg gtk-theme-breeze
	else
		add_packages adwaita-icon-theme breeze-gtk
	fi
}

gtk_preference() {
	if dark_preference; then
		dark=true
		theme=Breeze-Dark
	else
		dark=false
		theme=Breeze
	fi
	mkdir -p /etc/xdg/gtk-3.0
	printf '[Settings]\ngtk-application-prefer-dark-theme=%s\n' "$dark" \
		> /etc/xdg/gtk-3.0/settings.ini
	gtkrc=/usr/share/themes/$theme/gtk-2.0/gtkrc
	if [ -e "$gtkrc" ]; then
		mkdir -p /etc/gtk-2.0
		printf '# MultiPak: follows the host theme.\ninclude "%s"\n' "$gtkrc" \
			> /etc/gtk-2.0/gtkrc
	fi
}

case "${1:-}" in
	--prepare)
		desktop_packages
		gtk_preference
		;;
	*)
		echo "usage: $0 --prepare" >&2
		exit 2
		;;
esac
