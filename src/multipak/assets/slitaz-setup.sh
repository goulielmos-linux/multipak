#!/bin/sh
# MultiPak: prepare a SliTaz image before Distrobox sets it up.
# Distrobox does not know the tazpkg package manager, so this pre-init hook installs
# what Distrobox and MultiPak need and then marks the basic setup as done.
# With --upgrade, it updates the container instead, as distrobox upgrade cannot.
# With --recharge, it refreshes the package lists before an installation.
command -v tazpkg > /dev/null 2>&1 || exit 0
[ "$1" = "" ] && [ -e /.containersetupdone ] && exit 0
set -e
recharge() {
	tazpkg recharge
	# The mirror's bundle of lists can lag behind its packages.md5, and tazpkg then
	# rejects the newer packages with "Checksum error". Fetch the lists themselves.
	database=/var/lib/tazpkg
	mirror=$(cat "$database/mirror")
	for list in packages.info packages.list packages.md5 packages.equiv; do
		if wget -q -T 30 -O "$database/$list.new" "${mirror%/}/$list"; then
			mv -f "$database/$list.new" "$database/$list"
		else
			rm -f "$database/$list.new"
		fi
	done
}
# SliTaz's GTK 3.22 cannot use the host's GTK theme and falls back to light Adwaita,
# while the host may send it an icon theme made for dark backgrounds (white icons).
# Follow the host user's dark preference; launchers do not read /etc/profile.
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
case "$1" in
	--recharge)
		gtk_preference
		recharge
		exit 0
		;;
	--upgrade)
		gtk_preference
		recharge
		exec tazpkg up --auto
		;;
esac
# Busybox's wget does not verify HTTPS certificates. MultiPak's image carries GNU wget
# and the CA certificates (checked when the image was built): install them first.
bootstrap=/var/cache/multipak/bootstrap
for package in "$bootstrap"/*.tazpkg; do
	[ -e "$package" ] || continue
	tazpkg install "$package"
done
rm -rf "$bootstrap"
recharge
for package in sudo bash python3 xorg-xkeyboard-config ttf-dejavu mesa-dri glibc-locale; do
	tazpkg get-install "$package" || true
done
if ! command -v python3 > /dev/null 2>&1; then
	echo "MultiPak: python3 could not be installed from the SliTaz repository." >&2
	echo "The repository may be updating. Try again later." >&2
	exit 1
fi
# Graphical programs need the host's language; glibc-locale generates only en_US.
locale=$(sed -n 's/^LANG=["'\'']*\([^"'\'']*\).*/\1/p' /run/host/etc/locale.conf \
	/run/host/etc/default/locale 2> /dev/null | head -n 1)
case "$locale" in
	[a-z][a-z]_[A-Z][A-Z].UTF-8 | [a-z][a-z][a-z]_[A-Z][A-Z].UTF-8)
		localedef -i "${locale%%.*}" -c -f UTF-8 "$locale" || true
		;;
esac
# Busybox has no getent; Distrobox reads passwd entries through it.
if ! command -v getent > /dev/null 2>&1; then
	cat > /usr/bin/getent << 'EOF'
#!/bin/sh
# MultiPak: minimal getent for the passwd and group databases.
case "$1" in passwd | group) ;; *) exit 2 ;; esac
file=/etc/$1
[ $# -lt 2 ] && exec cat "$file"
shift
status=2
for key; do
	awk -F: -v k="$key" '$1 == k || $3 == k { print; found = 1; exit } END { exit !found }' \
		"$file" && status=0
done
exit $status
EOF
	chmod 755 /usr/bin/getent
fi
gtk_preference
# MultiPak runs the script again to refresh the lists and to update the container.
mkdir -p /usr/libexec
cp "$0" /usr/libexec/multipak-slitaz-setup.sh
touch /.containersetupdone
