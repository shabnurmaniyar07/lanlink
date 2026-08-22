package link.lan.android.server

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log

class ServerAdvertiser(private val context: Context) {
    private val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private var isRegistered = false

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
            setAttribute("version", "0.1.2")
            setAttribute("scheme", "https")
            setAttribute("fp", fingerprint.replace(":", "").take(32).lowercase())
        }

        try {
            nsd.registerService(serviceInfo, NsdManager.PROTOCOL_DNS_SD, registrationListener)
        } catch (e: Exception) {
            Log.e(TAG, "Could not register mDNS service", e)
        }
    }

    @Synchronized
    fun unregister() {
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
