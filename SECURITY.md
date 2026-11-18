# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private reporting instead:
<https://github.com/projectbin07-stack/stl-doctor/security/advisories/new>.

Include a description, a minimal input that triggers the problem and the version you used. Reports
are handled on a best-effort basis by a single maintainer.

## Scope

`stl-doctor` reads untrusted STL files and 3MF zip archives. It never executes file content, makes
no network requests and writes only to the paths given on the command line (`--report`, the repair
output, `orient -o`); `repair` refuses to overwrite its input. Reports of interest include crashes
or excessive memory/CPU use on crafted input (3MF parts larger than 512 MiB are refused; XML is
parsed with the standard library's expat), and HTML injection through file names or mesh names into
the generated report.
