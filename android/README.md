# LanLink for Android — Phase 5

Status: **the protocol core is written and tested. The app around it is not written yet.**

```
android/
  core/src/    the client: JSON, models, path rules, resume arithmetic,
               certificate pinning, invites, LanLinkClient
  core/test/   its own suite, plus the interop run against a real node
```

## Why the core comes first

The protocol core is where an Android client goes wrong in ways nobody notices
until a file is already corrupt: appending to a partial download the peer never
resumed, trusting a certificate that changed, building a local path out of a
remote listing. Those parts touch **no Android API at all**, so they compile and
run on a plain JVM — which means they can be tested here, now, rather than after
somebody has a phone in their hand.

Everything in `core/src` imports only the Kotlin standard library and the JDK:

- `java.net.HttpURLConnection` rather than OkHttp — present on every Android
  version, no dependency to keep current.
- `javax.net.ssl.X509ExtendedTrustManager` for pinning. Not `CertificatePinner`
  and not a Network Security Config, both of which pin against a hostname; here
  the pin is per device and learned at runtime (protocol §4).
- A ~200 line JSON reader rather than a serialization library. `org.json` exists
  on Android but not on a plain JVM, and the protocol returns a dozen flat
  objects.

A test enforces this: `test_the_core_uses_no_android_api` fails the build if an
`android.`, `androidx.` or third-party import appears.

## Running the tests

```bash
pip install kotlin-jupyter-kernel     # ships the Kotlin compiler; one time
python -m pytest tests/test_android_core.py -q
```

That compiles the core, runs its 41 offline checks, then starts a real Python
node with its own certificate and drives the **Kotlin** client against it over a
pinned TLS socket: pair, browse, create a folder, upload with an interrupted
resume and a checksum, download with byte ranges, read properties, rename, copy,
delete, and unpair. 18 further checks. The run cleans up after itself and a test
asserts the node is left exactly as it was found.

If the toolchain is missing the tests skip rather than fail. In Android Studio
you would use the bundled Kotlin instead; nothing here depends on this
particular compiler.

## What is deliberately not here yet

The Android application itself — Gradle project, manifest, Compose UI,
`NsdManager` discovery, SAF storage, the foreground transfer service. None of it
can be compiled or run in this container: there is no Android SDK and Maven,
Google's Maven and the Gradle distribution are all unreachable. Writing it here
would mean shipping several thousand lines of Kotlin that have never been
through a compiler, which is not worth much to anybody.

The next step is to build that shell in Android Studio, where it can be
compiled, and drop `core/src` in unchanged as the networking layer.

## Using the core from the app

```kotlin
// Pairing: look at the certificate first, show the owner a fingerprint to
// compare, and only then send the code.
val invite = Invite.parse(scannedQrText)
val certificate = CertificateProbe.fetch(invite.host, invite.port)
val fingerprint = Pinning.fingerprintOf(certificate)
showToUser(Pinning.shortFingerprint(fingerprint))   // "A1B2 C3D4 E5F6 0789"
if (invite.hasPin && invite.fingerprint != fingerprint) refuse()

val client = LanLinkClient(invite.baseUrl, socketFactory = Pinning.socketFactory(certificate))
when (val outcome = client.pair(installationId, Build.MODEL, invite.code)) {
    is PairOutcome.Paired -> store(outcome.token, Pinning.toPem(certificate))
    else -> if (outcome.isRetryable) keepWaiting() else show(outcome.message)
}

// Downloading: the decision matters more than the bytes.
client.download(shareId, entry.path, offset = bytesAlreadyOnDisk) { decision, body ->
    when (decision) {
        is ResumeDecision.Append -> appendFrom(decision.offset, body)
        is ResumeDecision.StartOver -> { discardPartial(); writeFresh(body) }
        ResumeDecision.Restart -> startAgainFromZero()
    }
}
```

Store the token with `EncryptedSharedPreferences`, keep the pinned PEM beside
it, and put every call on `Dispatchers.IO` — all of this blocks.

Read `docs/protocol/v1.md` before changing anything here. The section numbers in
the Kotlin comments point at it.
