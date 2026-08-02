# Why your computer warns about this download

darlaston is free software and the builds are **not signed
with a paid developer certificate**, so macOS and Windows both treat them as
coming from an unknown source. Nothing is wrong with the file. Here is what
you will see and what to do about it:

## macOS

You will see: _"darlaston" cannot be opened because Apple cannot check it
for malicious software._

To open it:

1. Drag darlaston into Applications, as usual.
2. Try to open it once. It will be refused. This step is required. The
   option below does not appear until macOS has blocked it.
3. Open **System Settings > Privacy & Security**, scroll down, and click
   **Open Anyway** next to the message about darlaston.
4. Confirm. macOS remembers, and it opens normally from then on.

On macOS 14 and earlier you could skip that by right-clicking the app and
choosing Open. macOS 15 removed that shortcut.

If you would rather use the terminal:

```sh
xattr -dr com.apple.quarantine /Applications/darlaston.app
```

## Windows

You will see a blue **Windows protected your PC** panel from SmartScreen.
Click **More info**, then **Run anyway**.

## Linux

No warning. You may need to make the AppImage executable first:

```sh
chmod +x darlaston-*.AppImage
./darlaston-*.AppImage
```
