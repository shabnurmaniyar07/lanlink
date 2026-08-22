package link.lan.android.server

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.nio.charset.StandardCharsets

class ServerAdvertiser(private val context: Context) {
    private val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private var isRegistered = false
    private var beaconJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO)

    private val registrationListener = object : NsdManager.RegistrationListener {
        override fun onServiceRegistered(serviceInfo: NsdServiceInfo) {
            isRegistered = true
            Log.i(TAG, "mDNS Service registered: ${serviceInfo.serviceName}")
        }

        override fun onRegistrationFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
            isRegistered = false
            Log.w(TAG, "mDNS Service registration failed: $errorCode")
        }

        override fun onServiceUnregistered(serviceInfo: NsdServiceInfo) {
            isRegistered = false
            Log.i(TAG, "mDNS Service unregistered")
        }

        override fun onUnregistrationFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
            Log.w(TAG, "mDNS Service unregistration failed: $errorCode")
        }
    }

    @Synchronized
    fun register(
        deviceId: String,
        deviceName: String,
        port: Int,
        fingerprint: String,
    ) {
        if (isRegistered) unregister()

        val serviceInfo = NsdServiceInfo().apply {
            serviceName = deviceName
            serviceType = "_lanlink._tcp."
            setPort(port)
            setAttribute("id", deviceId)
            setAttribute("name", deviceName)
            setAttribute("api", "v1")
            setAttribute("platform", "Android")
            setAttribute("version", "0.2.0")
            setAttribute("scheme", "https")
            setAttribute("fp", fingerprint.replace(":", "").take(32).lowercase())
        }

        try {
            nsd.registerService(serviceInfo, NsdManager.PROTOCOL_DNS_SD, registrationListener)
        } catch (e: Exception) {
            Log.e(TAG, "Could not register mDNS service", e)
        }

        // Broadcast UDP Beacon on port 8766 every 2 seconds
        beaconJob?.cancel()
        beaconJob = scope.launch {
            val json = JSONObject().apply {
                put("magic", "LANLINK_BEACON_V1")
                put("id", deviceId)
                put("name", deviceName)
                put("port", port)
                put("scheme", "https")
                put("platform", "Android")
                put("version", "0.2.0")
                put("fp", fingerprint.replace(":", "").take(32).lowercase())
                put("api", "v1")
            }.toString()
            val bytes = json.toByteArray(StandardCharsets.UTF_8)
            while (isActive) {
                try {
                    DatagramSocket().use { sock ->
                        sock.broadcast = true
                        val packet = DatagramPacket(bytes, bytes.size, InetAddress.getByName("255.255.255.255"), 8766)
                        sock.send(packet)
                    }
                } catch (_: Exception) {}
                delay(2000)
            }
        }
    }

    @Synchronized
    fun unregister() {
        beaconJob?.cancel()
        beaconJob = null
        if (!isRegistered) return
        try {
            nsd.unregisterService(registrationListener)
            isRegistered = false
        } catch (e: Exception) {
            Log.e(TAG, "Could not unregister mDNS service", e)
        }
    }

    companion object {
        private const val TAG = "ServerAdvertiser"
    }
}
