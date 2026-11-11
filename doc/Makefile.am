SUBDIRS =
BUILT_SOURCES =

dist_html_DATA =

dist_man_MANS = nread.1
html_pages = nread.1.html

if USE_NANORC
dist_man_MANS += nanorc.5
html_pages += nanorc.5.html
endif

nread.1.html: nread.1
	groff -t -mandoc -Thtml < $? > $@
nanorc.5.html: nanorc.5
	groff -t -mandoc -Thtml < $? > $@

if GROFF_HTML
BUILT_SOURCES += $(html_pages)
dist_html_DATA += $(html_pages)
endif

EXTRA_DIST = $(BUILT_SOURCES)
