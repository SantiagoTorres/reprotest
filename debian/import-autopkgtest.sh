#!/bin/sh

set -e

tag=${1:-4.4}
repo=${2:-../autopkgtest}

( cd "$repo" && git checkout "$tag" )

lib_imports="adt_testbed adtlog VirtSubproc"

ourpatch() {
	for i in $lib_imports; do
		sed -i -e 's/^import '"$i"'/from reprotest.lib import '"$i"'/g' "$1"
	done
	sed -i -e "s,'/usr/share/autopkgtest/lib',os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),g" "$1"
}

last_import=$(git log --pretty="format:%H" --grep='Import autopkgtest')

for i in $lib_imports; do
	target=reprotest/lib/"$i.py"
	cp "$repo/lib/$i.py" "$target"
	ourpatch "$target"
done

for i in "$repo/virt/"*; do
	if [ "$i" != "${i%.1}" ]; then continue; fi # man page
	target=reprotest/virt/$(basename "$i")
	cp "$i" "$target"
	ourpatch "$target"
done

echo "*** Import complete; run the following to commit it:"
echo "  git commit -m 'Import autopkgtest $tag' reprotest/lib/ reprotest/virt/"
echo "*** Afterwards, you should re-import the following patches:"
git log --oneline "${last_import}..HEAD" -- reprotest/lib/ reprotest/virt/
