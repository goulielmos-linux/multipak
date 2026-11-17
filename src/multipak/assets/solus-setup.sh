#!/bin/sh
# MultiPak: prepare and update a Solus environment (run as root or with sudo).
#
#   (no option)  pre-init hook: Distrobox does not know eopkg, so this installs what
#                Distrobox and MultiPak need and then marks the basic setup as done;
#   --prepare    before each installation: refresh the package lists, add an icon
#                theme, the SVG loader and the Breeze GTK theme, and follow the host's
#                light or dark preference;
#   --upgrade    update the whole environment, as distrobox upgrade cannot.
command -v eopkg > /dev/null 2>&1 || exit 0
set -eu

# The image installs its packages without their configuration scripts, so the links
# baselayout's scripts would make to os-release are missing. Distrobox and MultiPak
# identify the distribution through them.
os_release() {
	[ -e /etc/os-release ] || ln -sf ../usr/share/os-release/os-release /etc/os-release
	[ -e /usr/lib/os-release ] ||
		ln -sf ../../usr/share/os-release/os-release /usr/lib/os-release
}

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

# Light or dark, as on the host: GTK 3 through gtk-application-prefer-dark-theme, GTK 2
# by including Breeze or Breeze-Dark. The host's XSETTINGS sends an empty theme name,
# so a GTK 2 theme name would be ignored; an included gtkrc is not.
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

# The image has no icon theme nor SVG loader: toolbar icons would show as broken.
# Each package on its own and not essential: the application installs without them.
desktop_packages() {
	for package in adwaita-icon-theme librsvg breeze-gtk-theme; do
		eopkg install --yes-all --no-color "$package" > /dev/null 2>&1 ||
			echo "MultiPak: $package is not available." >&2
	done
}

case "${1:-}" in
	--prepare)
		os_release
		eopkg update-repo --no-color
		desktop_packages
		gtk_preference || echo "MultiPak: the GTK setting could not be written." >&2
		exit 0
		;;
	--upgrade)
		os_release
		eopkg update-repo --no-color
		eopkg upgrade --yes-all --no-color
		gtk_preference || echo "MultiPak: the GTK setting could not be written." >&2
		exit 0
		;;
	"") ;;
	*)
		echo "usage: $0 [--prepare | --upgrade]" >&2
		exit 2
		;;
esac

os_release
[ -e /.containersetupdone ] && exit 0
eopkg update-repo --no-color
# The packages Distrobox would install on a distribution it knows, with Python 3 for
# MultiPak's helper and the graphics libraries.
eopkg install --yes-all --no-color python3 bash sudo shadow util-linux procps-ng findutils \
	diffutils curl rsync xauth xkeyboard-config dejavu-fonts-ttf gnupg openssh iputils \
	less man-db pigz bc wget lsof time zip unzip mesalib vulkan
# Without /etc/sudoers, Solus's sudo reads its defaults, which include /etc/sudoers.d,
# where Distrobox grants the user sudo. A local /etc/sudoers replaces them.
if [ -e /etc/sudoers ] &&
	! grep -Eq '^[[:space:]]*[@#]includedir[[:space:]]+/etc/sudoers.d' /etc/sudoers; then
	printf '\n@includedir /etc/sudoers.d\n' >> /etc/sudoers
fi
touch /.containersetupdone
