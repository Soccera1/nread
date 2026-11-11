AUTOMAKE_OPTIONS = gnu no-dependencies

SUBDIRS = doc lib m4 po src

if USE_COLOR
SUBDIRS += syntax
endif

EXTRA_DIST = IMPROVEMENTS tests/reader.py

ACLOCAL_AMFLAGS = -I m4

showinfo:
	@ echo
	@ echo "  Global configuration: nreadrc, then nanorc, in @sysconfdir@ and /etc"
	@ echo "  Syntaxes get installed in: @PKGDATADIR@/"
	@ echo
