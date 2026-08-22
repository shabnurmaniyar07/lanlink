package link.lan.android.service

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import link.lan.android.data.SecureStore
import link.lan.core.LanLinkClient
import link.lan.core.Pinning
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

object ClipboardSyncCentre {
    private var lastLocalClip: String = ""
    private var lastRemoteClip: String = ""
    private val mainHandler = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(Dispatchers.IO)
    var isEnabled: Boolean = true

    fun init(context: Context) {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return
        clipboard.addPrimaryClipChangedListener {
            if (!isEnabled) return@addPrimaryClipChangedListener
            val clip = clipboard.primaryClip ?: return@addPrimaryClipChangedListener
            if (clip.itemCount == 0) return@addPrimaryClipChangedListener
            val text = clip.getItemAt(0).text?.toString() ?: return@addPrimaryClipChangedListener
            if (text.isEmpty() || text == lastRemoteClip || text == lastLocalClip) return@addPrimaryClipChangedListener
            lastLocalClip = text
            broadcastClipboard(context, text)
        }
    }

    fun getLastClipboard(context: Context): String {
        return lastLocalClip
    }

    fun setRemoteClipboard(context: Context, text: String) {
        if (!isEnabled || text.isEmpty() || text == lastRemoteClip || text == lastLocalClip) return
        lastRemoteClip = text
        mainHandler.post {
            val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return@post
            val clip = ClipData.newPlainText("LanLink", text)
            clipboard.setPrimaryClip(clip)
            Toast.makeText(context, "📋 Clipboard synced from Laptop", Toast.LENGTH_SHORT).show()
        }
    }

    private fun broadcastClipboard(context: Context, text: String) {
        scope.launch {
            val store = SecureStore.open(context)
            val known = store.load().all()
            for (dev in known) {
                try {
                    val url = URL("https://${dev.host}:${dev.port}/v1/clipboard")
                    val conn = url.openConnection() as javax.net.ssl.HttpsURLConnection
                    if (dev.certificatePem.isNotBlank()) {
                        conn.sslSocketFactory = Pinning.socketFactoryForPem(dev.certificatePem)
                    }
                    conn.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
                    conn.requestMethod = "POST"
                    conn.setRequestProperty("x-lanlink-token", dev.token)
                    conn.setRequestProperty("Content-Type", "application/json")
                    conn.connectTimeout = 3000
                    conn.readTimeout = 3000
                    conn.doOutput = true
                    val payload = """{"text":${link.lan.core.Json.quote(text)}}"""
                    conn.outputStream.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
                    conn.responseCode
                    conn.disconnect()
                } catch (_: Exception) {}
            }
        }
    }
}
