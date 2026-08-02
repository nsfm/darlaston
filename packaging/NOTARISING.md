# Why your computer warns about this download

darlaston is free software built by one person. The builds are **not signed
with a paid developer certificate**, so macOS and Windows both treat them as
coming from an unknown source. Nothing is wrong with the file. Here is what
you will see and what to do about it, followed by what it would take to make
the warnings go away.

## macOS

The app is ad-hoc signed, which is what lets it run at all on Apple silicon
-- an unsigned arm64 binary will not execute. It is **not notarised**, so
Gatekeeper does not recognise it.

You will see: *"darlaston" cannot be opened because Apple cannot check it
for malicious software.*

To open it:

1. Drag darlaston into Applications, as usual.
2. Try to open it once. It will be refused. This step is required -- the
   option below does not appear until macOS has blocked it.
3. Open **System Settings > Privacy & Security**, scroll down, and click
   **Open Anyway** next to the message about darlaston.
4. Confirm. macOS remembers, and it opens normally from then on.

On macOS 14 and earlier you could skip that by right-clicking the app and
choosing Open. macOS 15 removed that shortcut, so Privacy & Security is now
the way.

If you would rather use the terminal:

```sh
xattr -dr com.apple.quarantine /Applications/darlaston.app
```

That removes the quarantine flag the browser attached when it downloaded
the file, and the warning with it.

## Windows

You will see a blue **Windows protected your PC** panel from SmartScreen.
Click **More info**, then **Run anyway**.

Windows also decides how suspicious a file is partly by how many people
have run it before, so this gets quieter over time on its own.

## Linux

No warning. You may need to make the AppImage executable first:

```sh
chmod +x darlaston-*.AppImage
./darlaston-*.AppImage
```

## What it would take to remove these warnings

Recorded here so the decision is a decision rather than an oversight.

**macOS: about 99 USD a year.** An Apple Developer Program membership
provides a Developer ID certificate. The build would then be signed with
it, submitted to Apple's notary service, and the returned ticket stapled
to the app. Users would see only the ordinary "downloaded from the
internet, are you sure" dialog. Doing this in CI needs four secrets: the
certificate as a base64 `.p12`, its password, an App Store Connect API key,
and the team identifier. It is perhaps twenty lines of workflow on top of
what is already here, and the workflow is written so those steps slot in
without restructuring.

**Windows: roughly 200-400 USD a year** for an OV code signing
certificate, and it does not fully solve the problem -- OV certificates
still accumulate SmartScreen reputation from zero. An EV certificate skips
the reputation period and costs several times more, and requires a hardware
token, which CI cannot use without a cloud signing service.

**Neither is worth it yet.** They are recurring costs against a program
with no users, and the instructions above work. Revisit when somebody who
is not being personally walked through the install is trying to use it.
