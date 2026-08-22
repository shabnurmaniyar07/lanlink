package link.lan.android.server

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import link.lan.core.Pinning
import java.math.BigInteger
import java.security.KeyStore
import java.security.KeyPairGenerator
import java.security.cert.X509Certificate
import java.util.Date
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.security.auth.x500.X500Principal

/**
 * Manages the phone's hardware-backed TLS identity and SSLContext for hosting the server.
 */
object ServerCertificate {
    private const val ALIAS = "lanlink_server_key"
    private const val ANDROID_KEYSTORE = "AndroidKeyStore"

    fun getOrCreate(deviceName: String): ServerIdentity {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        if (!keyStore.containsAlias(ALIAS)) {
            val kpg = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, ANDROID_KEYSTORE)
            val spec = KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY,
            )
                .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_NONE)
                .setCertificateSubject(X500Principal("CN=$deviceName, O=LanLink"))
                .setCertificateSerialNumber(BigInteger.valueOf(System.currentTimeMillis()))
                .setCertificateNotBefore(Date(System.currentTimeMillis() - 86400000L))
                .setCertificateNotAfter(Date(System.currentTimeMillis() + 3650L * 86400000L))
                .build()
            kpg.initialize(spec)
            kpg.generateKeyPair()
        }

        val cert = keyStore.getCertificate(ALIAS) as X509Certificate
        val fingerprint = Pinning.fingerprintOf(cert)
        val shortFp = Pinning.shortFingerprint(fingerprint)
        val pem = Pinning.toPem(cert)

        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        kmf.init(keyStore, null)

        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(kmf.keyManagers, null, null)

        return ServerIdentity(
            certificate = cert,
            fingerprint = fingerprint,
            shortFingerprint = shortFp,
            pem = pem,
            sslContext = sslContext,
        )
    }
}

data class ServerIdentity(
    val certificate: X509Certificate,
    val fingerprint: String,
    val shortFingerprint: String,
    val pem: String,
    val sslContext: SSLContext,
)
