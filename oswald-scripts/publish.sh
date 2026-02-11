#! /bin/sh

cd `dirname $0`

exec >> logs/publish.log 2>&1

echo
echo
echo
echo "************************ `date` ************************"
echo
#set -x

#thost="l10n-bot@l10n-files.qt.io"
tdir="/var/www/html/l10n-files"
#target="$thost:$tdir"
root=$PWD
log=$PWD/logs/update
map=$PWD/branch-map.txt

#redo=false
redo=true

collect=

prepare() {
  sts=`grep -v '^#' $dir/_STATUS` || { echo "$dir has no/invalid _STATUS file!"; return 1; }
  test `echo "$sts" | wc -l` = 1 || { echo "$dir has inconsistent _STATUS file!"; return 1; }
  test "$sts" != ignore || { echo "$dir has _STATUS ignore."; return 1; }
  ver=`cd $dir && git symbolic-ref --short HEAD`
  test -n "$ver" || { echo "$dir is not on a branch!"; return 1; }
  current=${dir#*-}
  collect="$collect $dir:$ver:$sts"
}

upload() {
  if test -n "$thost"; then
    ssh $thost "rm -rf $tdir/$dir/new; mkdir -p $tdir/$dir/new"
    scp *.ts $target/$dir/new
  else
    rm -rf $tdir/$dir/new; mkdir -p $tdir/$dir/new
    cp *.ts $tdir/$dir/new
  fi
}

# Fetch -current first, as only they have real upstreams.
#false && \
for p in *-current; do (
  cd $p || exit
  #git fetch -p -q
  if test ${p#qt-} != $p; then
    for i in qt*; do
      if test -d $i/.git; then (
        cd $i
        $redo || exit
        git fetch -p -q
      ); fi
    done
  fi
); done

: > $map

qt_build=$root/qt-current_build/qtbase

#false && \
for dir in qt-old515 qt-old6? qt-current; do
  test -d $dir && prepare && (
    cd $dir || exit
    echo qt5 $ver $current >> $map
    $redo || exit
    echo Doing Qt $current...
    exec > $log-$dir.log 2>&1
    #( cd qttranslations/translations && git clean -fdq . )
    #false && \
    { git submodule foreach "{ test $current = current || git fetch -q -p; } && git reset --hard --no-recurse-submodules @{u}" || exit; }
    rm -f qttranslations/translations/*_untranslated.ts
    if test ${ver#5} != $ver; then
      cd $root/${dir}_build/qttranslations/translations
      make qmake || exit # workaround for *_wrapper.sh missing from gitignore
      make ts-all || exit

      cd $root/$dir/qttranslations/translations
      cat > qt_untranslated.ts <<EOF
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="">
    <dependencies>
        <dependency catalog="qtbase_"/>
        <dependency catalog="qtscript_"/>
        <dependency catalog="qtmultimedia_"/>
        <dependency catalog="qtxmlpatterns_"/>
    </dependencies>
</TS>
EOF
    else
      cmake --build $root/${dir}_build/qttranslations --parallel 1 --target ts-all || exit

      cd $root/$dir/qttranslations/translations
      cat > qt_untranslated.ts <<EOF
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="">
    <dependencies>
        <dependency catalog="qtbase_"/>
        <dependency catalog="qtmultimedia_"/>
    </dependencies>
</TS>
EOF
    fi
    rm -f *_en.ts

    # import qt4 translations
    for i in qt_??.ts qt_??_??.ts; do
      if test $(stat -c "%s" $i) -gt 1000; then
        lng=${i##qt_}
        lng=${lng%%.ts}
        PATH=$qt_build/bin:$PATH LD_LIBRARY_PATH=$qt_build/lib $root/split-qt-ts-prebuilt.pl $lng $ver >> $log-$dir.log 2>&1
      fi
    done

    upload
  )
done

#false && \
for dir in creator-stable creator-current; do
  prepare && (
    cd $dir || exit
    echo creator $ver $current >> $map
    $redo || exit
    echo Doing Creator $current...
    exec > $log-$dir.log 2>&1
    { test $current = current || git fetch -q -p; } && git reset --hard @{u} || exit
    rm -f share/qtcreator/translations/qtcreator_untranslated.ts
    # the system qt is too old (by one patch level ...).
    # we use that to avoid building all of qt.
    sed -i cmake/QtCreatorAPI.cmake -e 's/^set(IDE_QT_VERSION_MIN .*/set(IDE_QT_VERSION_MIN "6.4")/'
    cd $root/${dir}_build
    ## this will fail because of missing private modules ... for whatever reason.
    #cmake --build . -t cmake_check_build_system 2> /dev/null
    cmake --build . -t ts_all || exit
    cd $root/$dir/share/qtcreator/translations
    upload
  )
done

#false && \
for dir in ifw-stable ifw-current; do
  prepare && (
    cd $dir || exit
    echo ifw $ver $current >> $map
    $redo || exit
    echo Doing IFW $current...
    exec > $log-$dir.log 2>&1
    { test $current = current || git fetch -q -p; } && git reset --hard @{u} || exit
    rm -f src/sdk/translations/ifw_untranslated.ts
    cd $root/${dir}_build/src/sdk/translations
    make qmake || exit # refresh xml file lists
    make ts-all || exit
    cd $root/$dir/src/sdk/translations
    upload
  )
done

mb=`basename $map`
commit="for i in *; do test -n \"\$(ls -A \$i/new 2> /dev/null)\" || continue; mv -f \$i/new/* \$i/; done; mv -f index.html.new index.html; mv -f $mb.new $mb"

if test -n "$thost"; then
  scp $map $target/$mb.new
  ./check-ts.pl $collect | ssh $thost "cd $tdir; cat > index.html.new; $commit"
  # backup ourselves
  scp *.pl *.sh *.xq $thost:~/qt-l10n
else
  cp $map $tdir/$mb.new
  ./check-ts.pl $collect > $tdir/index.html.new
  cd $tdir
  eval "$commit"
fi
