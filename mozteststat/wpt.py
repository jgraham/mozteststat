def has_wpt_changes(paths):
    for path in paths:
        if ((path.startswith("testing/web-platform/tests/") and not
             (path.startswith("testing/web-platform/tests/tools/") or
              path.startswith("testing/web-platform/tests/resources/"))) or
            path.startswith("testing/web-platform/mozilla/tests/")):
            return True
    return False

def has_wpt_meta_changes(paths):
    for path in paths:
        if (path.startswith("testing/web-platform/meta/") or
            path.startswith("testing/web-platform/mozilla/meta/")):
            return True
    return False
