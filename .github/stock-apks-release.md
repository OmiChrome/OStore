**Inbox for original (unpatched) APKs.** Not an app download.

Some stores block GitHub's servers, so the builder can't always download an original app by itself.
When that happens, a `build-failure` issue names the exact package and version it needs.

1. Download that original APK on your phone or PC (APKMirror, or a Morphe download helper). arm64-v8a or universal; `.apk`, `.apkm` or `.apks`.
2. Keep the file name (it must start with the package name, e.g. `com.instagram.android_452.1.0.53.64...apkm`).
3. Edit this release → attach the file → Update release.
4. Run the **Build** workflow (or wait for the next scheduled run).

The builder only uses a file if it has the right package and version and, for apps in `signers.txt`, the real developer's signature.
