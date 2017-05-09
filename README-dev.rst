Running the Tests
=================

Tests are run with `Tox <https://pypi.python.org/pypi/tox>`__,
`pytest <https://pypi.python.org/pypi/pytest>`__ and
`Coverage <https://pypi.python.org/pypi/coverage>`__. On Debian, this
can be done with
``apt-get install python3-coverage python3-pytest tox``.

To run the tests, go to the root of the repository, where ``tox.ini`` is
and run ``tox``. For more verbose output, run ``tox -- -s``.

This runs the tests with no virtualization. To test that reprotest works
correctly with virtualization, you'll need to setup the virtualization
servers schroot and qemu.

Some of the following instructions rely on Debian utilities. For
schroot, run ``mk-sbuild --debootstrap-include=devscripts stable``. (If
you aren't on ``amd64``, you'll have to include ``--arch``.) For qemu,
first ``apt-get install autopkgtest vmdebootstrap qemu``, then run:

::

    $ vmdebootstrap --verbose --serial-console --distribution=sid \
        --customize=/usr/share/autopkgtest/setup-commands/setup-testbed \
        --user=adt/adt --size=10000000000 --grub --image=adt-sid.raw
    $ qemu-img convert -O qcow2 adt-sid.raw  adt-sid.img
    $ rm adt-sid.raw

The last two commands reduce the size of the image but aren't strictly
necessary. Move ``adt-sid.img`` to ``linux/`` under your home directory.

To log into the schroot and qemu containers, respectively, run:

::

    $ sudo schroot -c source:stable-amd64
    $ qemu-system-x86_64 -enable-kvm -drive file=~/linux/adt-sid.img,if=virtio \
        -net user -net nic,model=virtio -m 1024

After replacing ``~`` with your home directory.

For the host system and the two containers, run:

::

    $ apt-get install disorderfs
    (Additionally for mk-sbuild stable, enable the backports repository.)
    (Additionally for chroot, run:
    $ mknod -m 666 /dev/fuse c 10 229)
    $ apt-get install python3 python3-pip
    $ apt-get install locales-all

Now, finally run the tests:

::

    $ REPROTEST_TEST_SERVERS=null,qemu,schroot tox -- -s


Releasing
=========

After releasing (with ``gbp buildpackage``), please upload a signed tarball:

::

    $ TARBALL=$(dpkg-parsechangelog -SSource)_$(dpkg-parsechangelog -SVersion).tar.xz
    $ gpg --detach-sign --armor --output=../${TARBALL}.asc < ../${TARBALL}
    $ scp ../${TARBALL}* alioth.debian.org:/home/groups/reproducible/htdocs/releases/reprotest
