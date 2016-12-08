# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import collections
import os


class AttributeFunctor(collections.namedtuple('_AttributeFunctor', 'x f')):
    def __getattr__(self, name):
        return lambda *args: self.x._replace(**{
            name: self.f(getattr(self.x, name), *args)
        })


class ReprotestPreset(collections.namedtuple('_ReprotestPreset',
    'build_command artifact testbed_pre testbed_init')):
    """Named-tuple representing a reprotest command preset.

    You can manipulate it like this:

    >>> ReprotestPreset(None, None, None, None)
    ReprotestPreset(build_command=None, artifact=None, testbed_pre=None, testbed_init=None)

    >>> _.set.build_command("etc")
    ReprotestPreset(build_command='etc', artifact=None, testbed_pre=None, testbed_init=None)

    >>> _.append.build_command("; etc2")
    ReprotestPreset(build_command='etc; etc2', artifact=None, testbed_pre=None, testbed_init=None)

    >>> _.prepend.build_command("setup; ")
    ReprotestPreset(build_command='setup; etc; etc2', artifact=None, testbed_pre=None, testbed_init=None)

    >>> _.set.build_command("dpkg-buildpackage -us -uc -b")
    ReprotestPreset(build_command='dpkg-buildpackage -us -uc -b', artifact=None, testbed_pre=None, testbed_init=None)

    >>> _.str_replace.build_command(
    ...    "dpkg-buildpackage", "DEB_BUILD_OPTIONS=nocheck dpkg-buildpackage -Pnocheck")
    ReprotestPreset(build_command='DEB_BUILD_OPTIONS=nocheck dpkg-buildpackage -Pnocheck -us -uc -b', artifact=None, testbed_pre=None, testbed_init=None)
    """

    @property
    def set(self):
        """Set the given attribute to the given value."""
        return AttributeFunctor(self, lambda x, y: y)
    @property
    def str_replace(self):
        """Do a substring-replace on the given attribute."""
        return AttributeFunctor(self, str.replace)
    @property
    def prepend(self):
        """Prepend the given value to the given attribute."""
        return AttributeFunctor(self, lambda a, b: b + a)
    @property
    def append(self):
        """Apppend the given value to the given attribute."""
        return AttributeFunctor(self, lambda a, b: a + b)


PRESET_DEB_DIR = ReprotestPreset(
    build_command = 'dpkg-buildpackage -uc -us -b',
    artifact = '../*.deb',
    testbed_pre = None,
    testbed_init = None
)

def preset_deb_schroot(preset):
    return preset.str_replace.build_command("dpkg-buildpackage",
        'PATH=/sbin:/usr/sbin:$PATH apt-get -y --no-install-recommends build-dep ./; dpkg-buildpackage'
    ).set.testbed_init(
        'apt-get -y --no-install-recommends install util-linux disorderfs faketime locales-all 2>/dev/null; \
        test -c /dev/fuse || mknod -m 666 /dev/fuse c 10 229'
    )

def preset_deb_dsc(fn):
    return ReprotestPreset(
        build_command = 'dpkg-source -x "%s" build && cd build && dpkg-buildpackage -uc -us -b' % fn,
        artifact = '*.deb',
        testbed_pre = None,
        testbed_init = None
    )

def get_presets(buildfile, virtual_server):
    fn = os.path.basename(buildfile)
    parts = os.path.splitext(fn)
    if os.path.isdir(buildfile):
        if os.path.isdir(os.path.join(buildfile, "debian")):
            if virtual_server == "null":
                return PRESET_DEB_DIR
            else:
                return preset_deb_schroot(PRESET_DEB_DIR)
    elif os.path.isfile(buildfile):
        if parts[1] == '.dsc':
            if virtual_server == "null":
                return preset_deb_dsc(fn)
            else:
                return preset_deb_schroot(preset_deb_dsc(fn))
    raise ValueError("unrecognised file type: %s" % buildfile)
