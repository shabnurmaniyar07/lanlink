package link.lan.android.net

import android.annotation.SuppressLint
import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import link.lan.app.SeenDevice
import link.lan.core.DEFAULT_PORT
import link.lan.core.SERVICE_TYPE
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.SocketTimeoutException

/**
 * LanLink devices on this Wi-Fi, via NsdManager.
 *
 * The TXT record is the same one the desktop publishes: `id`, `name`, `api`,
 * `platform`, `version`, `scheme` and `fp`. Note that `fp` carries only the
 * first 32 characters of the fingerprint — a TXT record has little room — so it
 * is a hint for the list, never the pin. The real certificate check happens at
 * the TLS handshake in `link.lan.core.PinnedTrustManager`, and `mergeDevices`
 * knows to compare `fp` as a prefix.
 *
 * Everything here is Android API and therefore untested in CI. It is kept as
 * small as it can be for exactly that reason: it turns callbacks into a Flow of
 * [SeenDevice] and does no thinking of its own.
 */
class Discovery(context: Context) {

    private val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager

    /**
     * Devices as they appear and disappear, until the collector stops.
     *
     * Emits the whole current set on each change rather than deltas: the list
     * screen wants a picture, not a diff, and the set is a handful of items.
     */
    fun devices(): Flow<List<SeenDevice>> = callbackFlow {
        val found = LinkedHashMap<String, SeenDevice>()

        fun publish() {
            trySend(found.values.toList())
        }

        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {
                Log.i(TAG, "discovery started for $serviceType")
            }

            override fun onServiceFound(service: NsdServiceInfo) {
                Log.i(TAG, "service discovered: ${service.serviceName}")
                resolve(service) { seen ->
                    if (seen == null) {
                        Log.w(TAG, "service ${service.serviceName} could not be turned into a device")
                        return@resolve
                    }
                    // The fingerprint here is the first 32 characters only —
                    // a TXT record has little room. It is a hint for the list;
                    // the real pin is checked at the TLS handshake.
                    Log.i(
                        TAG,
                        "service resolved: name=${seen.name} address=${seen.host}:${seen.port} " +
                            "id=${seen.id} fp32=${seen.fingerprint.take(32).ifEmpty { "(none)" }}",
                    )
                    found[seen.id] = seen
                    publish()
                }
            }

            override fun onServiceLost(service: NsdServiceInfo) {
                // The lost record carries the service name, not our device id,
                // so match on the name we kept when it was resolved.
                val gone = found.entries.firstOrNull { it.value.name == service.serviceName }
                if (gone != null) {
                    Log.i(TAG, "service lost: ${service.serviceName}")
                    found.remove(gone.key)
                    publish()
                }
            }

            override fun onDiscoveryStopped(serviceType: String) {
                Log.i(TAG, "discovery stopped")
            }

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.w(TAG, "discovery could not start: $errorCode")
                close()
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.w(TAG, "discovery could not stop: $errorCode")
            }
        }

        val beaconJob = launch(Dispatchers.IO) {
            try {
                DatagramSocket(8766).use { socket ->
                    socket.broadcast = true
                    socket.soTimeout = 2000
                    val buf = ByteArray(2048)
                    while (isActive) {
                        try {
                            val packet = DatagramPacket(buf, buf.size)
                            socket.receive(packet)
                            val str = String(packet.data, packet.offset, packet.length, Charsets.UTF_8)
                            val json = org.json.JSONObject(str)
                            if (json.optString("magic") == "LANLINK_BEACON_V1") {
                                val devId = json.optString("id")
                                val name = json.optString("name").ifEmpty { "LanLink device" }
                                val port = json.optInt("port", DEFAULT_PORT)
                                val fp = json.optString("fp").lowercase()
                                val host = packet.address.hostAddress ?: continue
                                if (devId.isNotEmpty()) {
                                    Log.i(TAG, "beacon discovered: name=$name address=$host:$port id=$devId")
                                    found[devId] = SeenDevice(
                                        id = devId,
                                        name = name,
                                        host = host,
                                        port = port,
                                        fingerprint = fp
                                    )
                                    publish()
                                }
                            }
                        } catch (_: java.net.SocketTimeoutException) {
                        } catch (_: Exception) {
                            kotlinx.coroutines.delay(500)
                        }
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "UDP Beacon listener socket error: ${e.message}")
            }
        }

        try {
            nsd.discoverServices(SERVICE_TYPE_NSD, NsdManager.PROTOCOL_DNS_SD, listener)
        } catch (error: Exception) {
            Log.w(TAG, "discovery is unavailable", error)
            close(error)
        }

        awaitClose {
            beaconJob.cancel()
            runCatching { nsd.stopServiceDiscovery(listener) }
        }
    }

    /**
     * Turn a found service into an address and a TXT record.
     *
     * `resolveService` is deprecated from API 34 in favour of
     * `registerServiceInfoCallback`, and the replacement does not exist before
     * it. Both paths end in the same [SeenDevice].
     */
    @Suppress("DEPRECATION")
    private fun resolve(service: NsdServiceInfo, ready: (SeenDevice?) -> Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            resolveModern(service, ready)
            return
        }
        nsd.resolveService(
            service,
            object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                    Log.w(TAG, "could not resolve ${info.serviceName}: $errorCode")
                    ready(null)
                }

                override fun onServiceResolved(info: NsdServiceInfo) {
                    ready(seenDeviceOf(info))
                }
            },
        )
    }

    @SuppressLint("NewApi")
    private fun resolveModern(service: NsdServiceInfo, ready: (SeenDevice?) -> Unit) {
        val callback = object : NsdManager.ServiceInfoCallback {
            override fun onServiceInfoCallbackRegistrationFailed(errorCode: Int) {
                Log.w(TAG, "registration failed: $errorCode")
                ready(null)
            }

            override fun onServiceUpdated(info: NsdServiceInfo) {
                ready(seenDeviceOf(info))
                runCatching { nsd.unregisterServiceInfoCallback(this) }
            }

            override fun onServiceLost() = Unit

            override fun onServiceInfoCallbackUnregistered() = Unit
        }
        runCatching {
            nsd.registerServiceInfoCallback(service, { it.run() }, callback)
        }.onFailure {
            Log.w(TAG, "could not register a service callback", it)
            ready(null)
        }
    }

    @Suppress("DEPRECATION")
    private fun seenDeviceOf(info: NsdServiceInfo): SeenDevice? {
        val attributes = info.attributes.orEmpty()
        fun text(key: String): String =
            attributes[key]?.toString(Charsets.UTF_8).orEmpty().trim()

        val id = text("id")
        if (id.isEmpty()) return null

        // IPv4 only: the protocol's addresses are LAN v4 and a link-local v6
        // address would not be reachable in the way callers expect.
        val host = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            info.hostAddresses.filterIsInstance<Inet4Address>().firstOrNull()?.hostAddress
        } else {
            (info.host as? Inet4Address)?.hostAddress
        } ?: return null

        return SeenDevice(
            id = id,
            name = text("name").ifEmpty { info.serviceName.orEmpty() },
            host = host,
            port = info.port.takeIf { it > 0 } ?: DEFAULT_PORT,
            fingerprint = text("fp").lowercase(),
        )
    }

    companion object {
        private const val TAG = "LanLinkDiscovery"

        /** NsdManager wants the trailing dot; the protocol constant does not carry it. */
        private val SERVICE_TYPE_NSD = "$SERVICE_TYPE."
    }
}
