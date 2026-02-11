#!/usr/bin/perl -w
#############################################################################
##
## JSON output variant of check-ts.pl
## Generates structured JSON instead of HTML for the Qt Translation Dashboard.
##
## Usage: ./check-ts-json.pl qt-current:6.9:dev creator-current:15.0:dev ...
##
#############################################################################

use strict;
use POSIX 'strftime';
use JSON::PP;
use File::Basename;

my %states = (
  "dev"   => "Branch is under active development. Translating it is not recommended.",
  "soft"  => "Strings are soft-frozen for the upcoming release. Some changes are still possible.",
  "hard"  => "Strings are hard-frozen for the upcoming release. Recommended for translation.",
  "maint" => "Current stable branch. Recommended for translation.",
  "lts"   => "Long term supported stable branch. Recommended for translation.",
  "old"   => "Old stable branch. No further releases are planned, but distributors may still pick up changes.",
);

my $qt5 = $ENV{HOME} . "/qt-l10n/qt-old515_build/qtbase";
my $xmlpat = "LD_LIBRARY_PATH=$qt5/lib $qt5/bin/xmlpatterns";

my $script_dir = dirname(__FILE__);
my $branch_map_file = "$script_dir/branch-map.txt";

# Load branch-map.txt if available
my %branch_map;
if (-f $branch_map_file) {
    open my $fh, '<', $branch_map_file or warn "Cannot open $branch_map_file: $!";
    if ($fh) {
        while (<$fh>) {
            chomp;
            next if /^\s*#/ || /^\s*$/;
            if (/^(\S+)\s+(.+)$/) {
                $branch_map{$1} = $2;
            }
        }
        close $fh;
    }
}

my %result_data;       # version_id => { lang => { module => pct } }
my %result_templates;  # version_id => [ module_names ]
my @result_versions;

sub processQt {
    my ($subdir, $infix, $version, $state, $groups, $type) = @_;

    my %scores;
    my %langs;

    my $files = join("\n", <$subdir$infix/translations/*_??.ts>);
    my $res = `$xmlpat -param files=\"$files\" check-ts.xq`;
    for my $i (split(/[ \n]/, $res)) {
        next unless $i =~ /^(?:[^\/]+\/)*([^.]+)\.ts:(.*)$/;
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

    my $id = $subdir;
    my $name = "Qt $version";

    push @result_versions, {
        id               => $id,
        name             => $name,
        version          => $version,
        type             => $type,
        state            => $state,
        stateDescription => $states{$state} // "",
    };

    $result_data{$id} = {};
    for my $lang (sort keys %langs) {
        $result_data{$id}{$lang} = {};
        for my $g (@{$groups}) {
            my $pc = $scores{$g}{$lang};
            if (defined $pc) {
                $result_data{$id}{$lang}{$g} = $pc eq "n/a" ? -1 : int($pc);
            }
        }
    }

    my @templates;
    for my $g (@{$groups}) {
        if (-f $subdir . $infix . "/translations/" . $g . "_untranslated.ts") {
            push @templates, $g;
        }
    }
    $result_templates{$id} = \@templates;
}

sub doQt5json {
    my ($subdir, $version, $state) = @_;
    processQt($subdir, "/qttranslations", $version, $state,
        ["qt", "qtbase", "qtdeclarative", "qtquickcontrols", "qtquickcontrols2",
         "qtscript", "qtmultimedia", "qtxmlpatterns", "qtconnectivity",
         "qtlocation", "qtserialport", "qtwebengine", "qtwebsockets",
         "qt_help", "assistant", "designer", "linguist"], "qt");
}

sub doQt6json {
    my ($subdir, $version, $state) = @_;
    processQt($subdir, "/qttranslations", $version, $state,
        ["qt", "qtbase", "qtdeclarative", "qtmultimedia", "qtconnectivity",
         "qtlocation", "qtserialport", "qtwebengine", "qtwebsockets",
         "qt_help", "assistant", "designer", "linguist"], "qt");
}

sub doOtherJson {
    my ($dirname, $filename, $appname, $type, $subdir, $version, $state) = @_;

    my %scores;
    my $files = join("\n", <$subdir/$dirname/${filename}*_??.ts>);
    my $res = `$xmlpat -param files=\"$files\" check-ts.xq`;
    for my $i (split(/ /, $res)) {
        if ($i =~ /^(?:[^\/]+\/)*${filename}_(.*)\.ts:(.*)$/) {
            my ($lang, $pc) = ($1, $2);
            $scores{$lang} = $pc;
        }
    }

    my $id = $subdir;
    my $name = "$appname $version";

    push @result_versions, {
        id               => $id,
        name             => $name,
        version          => $version,
        type             => $type,
        state            => $state,
        stateDescription => $states{$state} // "",
    };

    $result_data{$id} = {};
    for my $lang (sort keys %scores) {
        my $pc = $scores{$lang};
        $result_data{$id}{$lang} = {
            $filename => ($pc eq "n/a" ? -1 : int($pc)),
        };
    }

    my @templates;
    if (-f "$subdir/$dirname/${filename}_untranslated.ts") {
        push @templates, $filename;
    }
    $result_templates{$id} = \@templates;
}

sub doCreatorJson { doOtherJson("share/qtcreator/translations", "qtcreator", "Qt Creator", "creator", @_); }
sub doIfwJson     { doOtherJson("src/sdk/translations", "ifw", "Qt Installer Framework", "ifw", @_); }

# Process all arguments
for my $ent (@ARGV) {
    $ent =~ /^([\w-]+):([\w.]+):(\w+)$/ or die("Malformed entity '$ent'.\n");
    my ($subdir, $version, $state) = ($1, $2, $3);
    if ($subdir =~ /^qt-/) {
        if ($version =~ /^5/) { doQt5json($subdir, $version, $state); }
        elsif ($version =~ /^6/) { doQt6json($subdir, $version, $state); }
        else { die("Unsupported Qt version in '$ent'."); }
    } elsif ($subdir =~ /^creator-/) {
        doCreatorJson($subdir, $version, $state);
    } elsif ($subdir =~ /^ifw-/) {
        doIfwJson($subdir, $version, $state);
    } else {
        die("Unsupported product in '$ent'.");
    }
}

# Build output
my %output = (
    generated  => strftime("%Y-%m-%dT%H:%M:%SZ", gmtime(time)),
    versions   => \@result_versions,
    data       => \%result_data,
    templates  => \%result_templates,
);

if (%branch_map) {
    $output{branchMap} = \%branch_map;
}

my $json = JSON::PP->new->utf8->canonical->pretty->encode(\%output);
print $json;
