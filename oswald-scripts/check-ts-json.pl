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

# Try XML::LibXML, fall back to regex parsing
my $HAS_LIBXML = eval { require XML::LibXML; 1 };

# Parse a .ts file and return ($translated_count, $total_count)
sub parse_ts_file {
    my ($file) = @_;
    if ($HAS_LIBXML) {
        return _parse_ts_libxml($file);
    } else {
        return _parse_ts_regex($file);
    }
}

sub _parse_ts_libxml {
    my ($file) = @_;
    my $doc = XML::LibXML->load_xml(location => $file);
    my $total = 0;
    my $translated = 0;
    for my $msg ($doc->findnodes('//message')) {
        my ($tr) = $msg->findnodes('translation');
        next unless $tr;
        my $type = $tr->getAttribute('type') // '';
        next if $type eq 'obsolete' || $type eq 'vanished';
        $total++;
        $translated++ unless $type eq 'unfinished';
    }
    return ($translated, $total);
}

sub _parse_ts_regex {
    my ($file) = @_;
    open my $fh, '<', $file or return (0, 0);
    my $content = do { local $/; <$fh> };
    close $fh;
    my $total = 0;
    my $translated = 0;
    # Match each <message>...</message> block
    while ($content =~ /<message[^>]*>(.*?)<\/message>/gs) {
        my $block = $1;
        if ($block =~ /<translation\s+type="(obsolete|vanished)"/) {
            next;
        }
        $total++;
        if ($block =~ /<translation\s+type="unfinished"/) {
            # not translated
        } else {
            $translated++;
        }
    }
    return ($translated, $total);
}

# Get percentage string for a .ts file
sub ts_percentage {
    my ($file) = @_;
    my ($translated, $total) = parse_ts_file($file);
    return "n/a" if $total == 0;
    return int($translated * 100 / $total);
}

my %states = (
  "dev"   => "Branch is under active development. Translating it is not recommended.",
  "soft"  => "Strings are soft-frozen for the upcoming release. Some changes are still possible.",
  "hard"  => "Strings are hard-frozen for the upcoming release. Recommended for translation.",
  "maint" => "Current stable branch. Recommended for translation.",
  "lts"   => "Long term supported stable branch. Recommended for translation.",
  "old"   => "Old stable branch. No further releases are planned, but distributors may still pick up changes.",
);

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
my %result_templates;  # version_id => { module => template_path }
my %result_files;      # version_id => { lang => { module => file_path } }
my @result_versions;

sub processQt {
    my ($subdir, $infix, $version, $state, $groups, $type) = @_;

    my %scores;
    my %langs;
    my %file_paths;

    my $trans_dir = "$subdir$infix/translations";
    for my $file (glob("$trans_dir/*_??.ts")) {
        my $bn = basename($file, ".ts");
        for my $g (@{$groups}) {
            if ($bn =~ /^${g}_((.._)?..)$/) {
                my $lang = $1;
                my $pc = ts_percentage($file);
                $scores{$g}{$lang} = $pc;
                $file_paths{$g}{$lang} = $file;
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
    $result_files{$id} = {};
    for my $lang (sort keys %langs) {
        $result_data{$id}{$lang} = {};
        $result_files{$id}{$lang} = {};
        for my $g (@{$groups}) {
            my $pc = $scores{$g}{$lang};
            if (defined $pc) {
                $result_data{$id}{$lang}{$g} = $pc eq "n/a" ? -1 : int($pc);
                $result_files{$id}{$lang}{$g} = $file_paths{$g}{$lang};
            }
        }
    }

    my %templates;
    for my $g (@{$groups}) {
        my $tpl = "$trans_dir/${g}_untranslated.ts";
        if (-f $tpl) {
            $templates{$g} = $tpl;
        }
    }
    $result_templates{$id} = \%templates;
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
    my %file_paths;
    my $trans_dir = "$subdir/$dirname";
    for my $file (glob("$trans_dir/${filename}_??.ts"), glob("$trans_dir/${filename}_??_??.ts")) {
        if (basename($file) =~ /^${filename}_(.+)\.ts$/) {
            my $lang = $1;
            next if $lang eq 'untranslated';
            $scores{$lang} = ts_percentage($file);
            $file_paths{$lang} = $file;
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
    $result_files{$id} = {};
    for my $lang (sort keys %scores) {
        my $pc = $scores{$lang};
        $result_data{$id}{$lang} = {
            $filename => ($pc eq "n/a" ? -1 : int($pc)),
        };
        $result_files{$id}{$lang} = {
            $filename => $file_paths{$lang},
        };
    }

    my %templates;
    my $tpl = "$trans_dir/${filename}_untranslated.ts";
    if (-f $tpl) {
        $templates{$filename} = $tpl;
    }
    $result_templates{$id} = \%templates;
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
    files      => \%result_files,
    templates  => \%result_templates,
);

if (%branch_map) {
    $output{branchMap} = \%branch_map;
}

my $json = JSON::PP->new->utf8->canonical->pretty->encode(\%output);
print $json;
