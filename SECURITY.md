# Security Policy

## Supported versions

The latest release. This is a young project with one maintainer; older
releases are not patched.

## Reporting a vulnerability

Report privately via
[GitHub Security Advisories](https://github.com/nsfm/darlaston/security/advisories/new).

Please do not open a public issue for a vulnerability.

## Scope

darlaston is a desktop application that drives a camera over USB and
writes image files. It does not make network requests, does not store
credentials, and has no server component. That narrows the realistic
surface a great deal, and the parts that remain are:

- **A vendor SDK loaded at runtime.** darlaston never bundles the camera
  SDK; it `dlopen`s whichever one the user installed, from
  `$TOUPCAM_SDK`, from `~/toup/sdk-*/`, or from the directory its own
  installer wrote. A user who is tricked into unpacking a hostile library
  in one of those places is running that library's code. This is the same
  trust model as installing the vendor's own software, but it is worth
  stating.
- **Reading files somebody else produced.** DNG, TIFF and JPEG files, and
  the JSON in `~/.config/darlaston`, are parsed on open. Malformed input
  here should raise, not execute.
- **Dependencies.** numpy, OpenCV and PySide6, and nothing else at
  runtime. OpenCV's image decoders are the largest piece of third-party
  parsing in the program.

## Things that are working as intended

- **The builds are unsigned**, so macOS and Windows warn about them. That
  is a cost decision, written up in `packaging/NOTARISING.md`, not an
  oversight. Verify a download by checking it came from
  [the releases page](https://github.com/nsfm/darlaston/releases).
- **Captured files carry metadata about your setup** -- objective,
  illumination, exposure, and any subject and slide notes you typed.
  That is the point of the program. Check what a file contains before
  publishing it if that matters to you.
