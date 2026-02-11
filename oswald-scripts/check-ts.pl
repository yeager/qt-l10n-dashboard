#! /usr/bin/perl -w
#############################################################################
##
## Copyright (C) 2010 Nokia Corporation and/or its subsidiary(-ies).
## All rights reserved.
## Contact: Nokia Corporation (qt-info@nokia.com)
##
## This file is part of the translations module of the Qt Toolkit.
##
## $QT_BEGIN_LICENSE:LGPL$
## No Commercial Usage
## This file contains pre-release code and may not be distributed.
## You may use this file in accordance with the terms and conditions
## contained in the Technology Preview License Agreement accompanying
## this package.
##
## GNU Lesser General Public License Usage
## Alternatively, this file may be used under the terms of the GNU Lesser
## General Public License version 2.1 as published by the Free Software
## Foundation and appearing in the file LICENSE.LGPL included in the
## packaging of this file.  Please review the following information to
## ensure the GNU Lesser General Public License version 2.1 requirements
## will be met: http://www.gnu.org/licenses/old-licenses/lgpl-2.1.html.
##
## In addition, as a special exception, Nokia gives you certain additional
## rights.  These rights are described in the Nokia Qt LGPL Exception
## version 1.1, included in the file LGPL_EXCEPTION.txt in this package.
##
## If you have questions regarding the use of this file, please contact
## Nokia at qt-info@nokia.com.
##
##
##
##
##
##
##
##
## $QT_END_LICENSE$
##
#############################################################################


use strict;
use POSIX 'strftime';

my %states = (
  "dev" => "Branch is under active development. Translating it is <b>not</b> recommended.",
  "soft" => "Strings are soft-frozen for the upcoming release. Some changes are still possible.",
  "hard" => "Strings are hard-frozen for the upcoming release. Recommended for translation.",
  "maint" => "Current stable branch. Recommended for translation.",
  "lts" => "Long term supported stable branch. Recommended for translation.",
  "old" => "Old stable branch. No further releases are planned, but distributors may still pick up changes."
);

my $qt5 = $ENV{HOME}."/qt-l10n/qt-old515_build/qtbase";
my $xmlpat = "LD_LIBRARY_PATH=$qt5/lib $qt5/bin/xmlpatterns";

sub doQt($$$$$)
{
  my ($subdir, $infix, $version, $state, $groups) = @_;

  my %scores = ();
  my %langs = ();

  my $files = join("\n", <$subdir$infix/translations/*_??.ts>);
  my $res = `$xmlpat -param files=\"$files\" check-ts.xq`;
  for my $i (split(/[ \n]/, $res)) {
    $i =~ /^(?:[^\/]+\/)*([^.]+)\.ts:(.*)$/;
    my ($fn, $pc) = ($1, $2);
    for my $g (@{$groups}) {
      if ($fn =~ /^${g}_((.._)?..)$/) {
        my $lang = $1;
        $scores{$g}{$lang} = $pc;
        $langs{$lang} = 1;
        last;
      }
    }
  }

  print "

<h2>Qt ".$version."</h2>

<p><i>".$states{$state}."</i></p>

";
  for my $lang (sort(keys(%langs))) {
    printf "<p><b>".$lang."</b>:";
    for my $g (@{$groups}) {
      my $pc = $scores{$g}{$lang};
      if (defined($pc)) {
        print " &nbsp; &nbsp; <a href=\"".$subdir."/".$g."_".$lang.".ts\">".$g."</a>&nbsp;(".($pc eq "n/a" ? $pc : $pc."%").")";
      }
    }
    print "</p>\n";
  }

  printf "<p><b>Templates</b>:";
  for my $g (@{$groups}) {
    if (-f $subdir.$infix."/translations/".$g."_untranslated.ts") {
      print " &nbsp; &nbsp; <a href=\"".$subdir."/".$g."_untranslated.ts\">".$g."</a>";
    }
  }
  print "</p>\n";
}

sub doQt5($$$)
{
  my ($subdir, $version, $state) = @_;
  doQt($subdir, "/qttranslations", $version, $state, ["qt", "qtbase", "qtdeclarative", "qtquickcontrols", "qtquickcontrols2", "qtscript", "qtmultimedia", "qtxmlpatterns", "qtconnectivity", "qtlocation", "qtserialport", "qtwebengine", "qtwebsockets", "qt_help", "assistant", "designer", "linguist"]);
}

sub doQt6($$$)
{
  my ($subdir, $version, $state) = @_;
  doQt($subdir, "/qttranslations", $version, $state, ["qt", "qtbase", "qtdeclarative", "qtmultimedia", "qtconnectivity", "qtlocation", "qtserialport", "qtwebengine", "qtwebsockets", "qt_help", "assistant", "designer", "linguist"]);
}

sub doOther()
{
  my ($dirname, $filename, $appname, $subdir, $version, $state) = @_;

  my %scores = ();

  my $files = join("\n", <$subdir/$dirname/${filename}*_??.ts>);
  my $res = `$xmlpat -param files=\"$files\" check-ts.xq`;
  for my $i (split(/ /, $res)) {
    $i =~ /^(?:[^\/]+\/)*${filename}_(.*)\.ts:(.*)$/;
    my ($lang, $pc) = ($1, $2);
    $scores{$lang} = $pc;
  }

  print "

<h2>".$appname." ".$version."</h2>

<p><i>".$states{$state}."</i></p>

<p>
";
  for my $lang (sort(keys(%scores))) {
    my $pc = $scores{$lang};
    print " &nbsp; &nbsp; <a href=\"".$subdir."/${filename}_".$lang.".ts\">".$lang."</a>&nbsp;(".$pc."%)";
  }
  print " &nbsp; &nbsp; <a href=\"".$subdir."/${filename}_untranslated.ts\">Template</a>
</p>
";
}

sub doCreator($$$) { &doOther("share/qtcreator/translations", "qtcreator", "Qt Creator", @_); }
sub doIfw($$$) { &doOther("src/sdk/translations", "ifw", "Qt Installer Framework", @_); }

print "<html>
<head><title>Qt and Qt Creator translation files</title></head>
<body>
";
# YEP, the manual map is gone. Edit _STATUS in the subdirectories instead.
# The dispatching is done in publish.sh.
for my $ent (@ARGV) {
  $ent =~ /^([\w-]+):([\w.]+):(\w+)$/ or die("Malformed entity '$ent'.\n");
  my ($subdir, $version, $state) = ($1, $2, $3);
  if ($subdir =~ /^qt-/) {
    if ($version =~ /^5/) {
      doQt5($subdir, $version, $state);
    } elsif ($version =~ /^6/) {
      doQt6($subdir, $version, $state);
    } else {
      die("Unsupported Qt version in '$ent'.");
    }
  } elsif ($subdir =~ /^creator-/) {
    doCreator($subdir, $version, $state);
  } elsif ($subdir =~ /^ifw-/) {
    doIfw($subdir, $version, $state);
  } else {
    die("Unsupported product in '$ent'.");
  }
}
print "
<p><small>Generated on ".strftime("%a, %d %b %Y %H:%M:%S %z", localtime(time))."</small></p>
</body>
</html>
";
