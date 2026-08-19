package link.lan.core

import java.io.ByteArrayInputStream
import java.net.Socket
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSession
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManager
import javax.net.ssl.X509ExtendedTrustManager
import javax.net.ssl.X509TrustManager

/**
 * §4: trust exactly one certificate, and nothing else.
 *
 * Not `CertificatePinner` and not a Network Security Config — both pin against
 * a hostname, and here the pin is per device and learned at runtime. Not the
 * system CA store either: these certificates are self-signed by design, so CA
 * validation would fail on a genuine peer and pass on nobody.
 *
 * Only JDK classes, so this is the same code on Android and on a plain JVM.
 */
object Pinning {

    /** SHA-256 of the certificate in DER form, lowercase hex — the identity. */
    fun fingerprintOf(certificate: X509Certificate): String =
        MessageDigest.getInstance("SHA-256").digest(certificate.encoded).toHex()

    fun fingerprintOfPem(pem: String): String = fingerprintOf(parsePem(pem))

    /**
     * §4: the first 16 characters, uppercase, in groups of four — what a person
     * compares against the other device's screen. The same grouping the desktop
     * shows, so the two really can be read side by side.
     */
    fun shortFingerprint(fingerprint: String): String =
        fingerprint.replace(":", "").take(16).uppercase().chunked(4).joinToString(" ")

    fun parsePem(pem: String): X509Certificate {
        val factory = CertificateFactory.getInstance("X.509")
        val bytes = pem.trim().toByteArray(Charsets.US_ASCII)
        return factory.generateCertificate(ByteArrayInputStream(bytes)) as X509Certificate
    }

    fun toPem(certificate: X509Certificate): String {
        val body = java.util.Base64.getMimeEncoder(64, "\n".toByteArray())
            .encodeToString(certificate.encoded)
        return "-----BEGIN CERTIFICATE-----\n$body\n-----END CERTIFICATE-----\n"
    }

    /** An SSL factory that accepts [pinned] and refuses everything else. */
    fun socketFactory(pinned: X509Certificate): SSLSocketFactory {
        val context = SSLContext.getInstance("TLS")
        context.init(null, arrayOf<TrustManager>(PinnedTrustManager(pinned)), null)
        return context.socketFactory
    }

    fun socketFactoryForPem(pem: String): SSLSocketFactory = socketFactory(parsePem(pem))

    /**
     * §4: hostname verification is off, because a LAN address changes and the
     * certificate is the identity. This is only safe *because* the trust manager
     * above accepts a single known certificate; it must never be paired with a
     * permissive trust manager.
     */
    fun hostnameVerifier(): HostnameVerifier = HostnameVerifier { _: String?, _: SSLSession? -> true }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
}

/**
 * Accepts one certificate, by exact bytes.
 *
 * Extends the *extended* trust manager so Android and the JDK do not silently
 * fall back to their own hostname checks on the socket-aware overloads.
 */
class PinnedTrustManager(private val pinned: X509Certificate) : X509ExtendedTrustManager() {

    private val expected: String = Pinning.fingerprintOf(pinned)

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val presented = chain?.firstOrNull()
            ?: throw CertificateException("The other device presented no certificate.")
        val actual = Pinning.fingerprintOf(presented)
        if (actual != expected) {
            throw CertificateMismatch(expected = expected, actual = actual)
        }
    }

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?, socket: Socket?) =
        checkServerTrusted(chain, authType)

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?, engine: javax.net.ssl.SSLEngine?) =
        checkServerTrusted(chain, authType)

    // A LanLink client never authenticates itself with a certificate; the token
    // is what identifies it. Refuse rather than quietly accept anything.
    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
        throw CertificateException("LanLink does not use client certificates.")

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?, socket: Socket?) =
        checkClientTrusted(chain, authType)

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?, engine: javax.net.ssl.SSLEngine?) =
        checkClientTrusted(chain, authType)

    override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf(pinned)
}

/**
 * §4: refuse, and do not offer to continue. Either the peer reinstalled — in
 * which case the owner re-pairs deliberately — or somebody is impersonating it.
 */
class CertificateMismatch(val expected: String, val actual: String) : CertificateException(
    "This device is not the one LanLink paired with. Its certificate changed, " +
        "so the connection was refused. Pair again only if you know it was reinstalled."
)

/**
 * Read a peer's certificate before trusting it, so the owner can be shown a
 * fingerprint to compare. Used once, during pairing, and never for transfers.
 */
object CertificateProbe {

    fun fetch(host: String, port: Int, timeoutMillis: Int = 8000): X509Certificate {
        val context = SSLContext.getInstance("TLS")
        context.init(null, arrayOf<TrustManager>(AcceptAnyForProbe()), null)
        val socket = context.socketFactory.createSocket() as SSLSocket
        socket.use {
            it.soTimeout = timeoutMillis
            it.connect(java.net.InetSocketAddress(host, port), timeoutMillis)
            it.startHandshake()
            val chain = it.session.peerCertificates
            return chain.firstOrNull() as? X509Certificate
                ?: throw CertificateException("The other device presented no certificate.")
        }
    }

    /**
     * Deliberately permissive, and deliberately not reachable from anywhere
     * else: it exists only to *look at* a certificate, before any token is sent
     * and before the owner has confirmed anything.
     */
    private class AcceptAnyForProbe : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }
}
