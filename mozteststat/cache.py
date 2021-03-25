import logging

def path_cache(func):
    _func_cache = {}
    def inner(path, obj):
        if path in _func_cache:
            cache_obj, cache_result = _func_cache[path]
            if cache_obj == obj:
                logging.debug("Getting %s from cache" % path)
                return cache_result
        rv = func(path, obj)
        _func_cache[path] = (obj, rv)
        return rv

    inner.__name__ = func.__name__
    inner.__doc__ = func.__doc__

    return inner
