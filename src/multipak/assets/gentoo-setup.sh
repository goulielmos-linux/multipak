#!/bin/sh
# MultiPak: prepare a Gentoo stage3 image before Distrobox sets it up.
# Distrobox would compile its basic packages from source. This pre-init hook fetches
# the package tree, switches to the desktop profile, turns on Gentoo's official binary
# packages, installs what Distrobox and MultiPak need and marks the basic setup as done.
# With --sync, it refreshes the package tree before an installation (at most once a day).
# With --install FILE CATEGORY PF, it installs a .gpkg.tar and its dependencies.
# With --upgrade, it updates the container instead, as distrobox upgrade cannot.
command -v emerge > /dev/null 2>&1 || exit 0
[ "$1" = "" ] && [ -e /.containersetupdone ] && exit 0
set -e
# A copy of a downloaded package is placed here, where emerge accepts it.
pkgdir=/var/cache/multipak/binpkgs
synced=/var/cache/multipak/synced
# Gentoo's mirror over HTTPS, with the snapshot's signature checked by emerge-webrsync.
sync_tree() {
	if [ "$1" = --if-old ] && [ -n "$(find "$synced" -mmin -1440 2> /dev/null)" ]; then
		return 0
	fi
	emerge-webrsync --quiet
	mkdir -p "${synced%/*}"
	touch "$synced"
}
# Downloaded binary packages are not needed once installed.
clean_cache() {
	rm -rf "$pkgdir"
	find /var/cache/binhost -name '*.gpkg.tar' -type f -delete 2> /dev/null || true
}
# Programs built with GTK 3 fall back to light Adwaita, while the host may send them an
# icon theme made for dark backgrounds. Follow the host user's dark preference.
gtk_preference() {
	# MultiPak runs the later modes with sudo; the first setup runs the user's own copy.
	owner=${SUDO_UID:-$(stat -c %u "$0" 2> /dev/null)}
	[ -n "$owner" ] || return 0
	dark=false
	for settings in /run/host/home/*/.config/gtk-3.0/settings.ini; do
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
emerge_binary() {
	emerge --ask=n --quiet-build --usepkg --with-bdeps=n "$@"
}
case "$1" in
	--sync)
		gtk_preference
		sync_tree --if-old
		exit 0
		;;
	--install)
		category=$3
		pf=$4
		# The names come from the package's metadata, already checked by MultiPak.
		case "$category/$pf" in
			*[!A-Za-z0-9_.+/-]* | .* | */.* | */*/* | */ | /*)
				echo "MultiPak: invalid Gentoo package name." >&2
				exit 2
				;;
		esac
		clean_cache
		mkdir -p "$pkgdir/$category"
		cp "$2" "$pkgdir/$category/$pf.gpkg.tar"
		# emerge installs a package file only from PKGDIR, at the path it would give it.
		export PKGDIR="$pkgdir" FEATURES="-binpkg-multi-instance"
		emaint binhost --fix > /dev/null
		status=0
		emerge_binary "$pkgdir/$category/$pf.gpkg.tar" || status=$?
		clean_cache
		exit $status
		;;
	--upgrade)
		gtk_preference
		sync_tree
		status=0
		emerge_binary --update --deep --newuse @world || status=$?
		clean_cache
		exit $status
		;;
esac
sync_tree
# Gentoo builds its binary packages for the desktop profile; with the base profile,
# graphical programs would have to be compiled.
case "$(readlink /etc/portage/make.profile)" in
	*/desktop | */desktop/*) ;;
	*) eselect profile set default/linux/amd64/23.0/desktop ;;
esac
# The image already lists Gentoo's binary repository, with signatures checked.
grep -q 'MultiPak' /etc/portage/make.conf || cat >> /etc/portage/make.conf << 'EOF'

# MultiPak: use Gentoo's official binary packages when they match.
FEATURES="${FEATURES} getbinpkg"
EOF
getuto
emerge_binary --noreplace app-admin/sudo app-shells/bash-completion sys-process/lsof \
	sys-devel/bc net-misc/wget app-crypt/pinentry x11-misc/xkeyboard-config media-fonts/dejavu
# Graphical programs need the host's language; GTK falls back to C without it.
locale=$(sed -n 's/^LANG=["'\'']*\([^"'\'']*\).*/\1/p' /run/host/etc/locale.conf \
	/run/host/etc/default/locale 2> /dev/null | head -n 1)
case "$locale" in
	[a-z][a-z]_[A-Z][A-Z].UTF-8 | [a-z][a-z][a-z]_[A-Z][A-Z].UTF-8)
		grep -qx "$locale UTF-8" /etc/locale.gen || echo "$locale UTF-8" >> /etc/locale.gen
		;;
esac
grep -qx 'en_US.UTF-8 UTF-8' /etc/locale.gen || echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen
locale-gen || true
clean_cache
gtk_preference
# MultiPak runs the script again to sync, install and update the container.
mkdir -p /usr/libexec
cp "$0" /usr/libexec/multipak-gentoo-setup.sh
touch /.containersetupdone
