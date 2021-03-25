# mozteststat

A tool to figure out which test types are being authored in
mozilla-central.

This requires a git clone of mozilla-central/unified that's created
with git-cinnabar. Currently the only supported test types are
crashtest, mochitest[-plain], reftest and web-platform-tests.
