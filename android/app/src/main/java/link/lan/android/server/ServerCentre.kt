package link.lan.android.server

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.os.Build
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import link.lan.android.data.SecureStore
import link.lan.core.Invite
import java.net.Inet4Address
import java.net.NetworkInterface

data class ServerState(
    val isRunning: Boolean = false,
    val ipAddress: String = "",
    val networkName: String = "Wi-Fi",
    val port: Int = 8765,
    val url: String = "",
    val deviceName: String = "",
    val deviceId: String = "",
    val fingerprint: String = "",
    val shortFingerprint: String = "",
    val isArmed: Boolean = false,
    val pairingCode: String = "",
    val pairingSecondsLeft: Int = 0,
    val inviteUrl: String = "",
    val shares: List<LocalShare> = emptyList(),
)

object ServerCentre {
    private const val TAG = "ServerCentre"
    private var server: LanLinkServer? = null
    private var advertiser: ServerAdvertiser? = null
    private var shareStore: LocalShareStore? = null
    private var secureStore: SecureStore? = null
    private var identity: ServerIdentity? = null
    private var connectivityManager: ConnectivityManager? = null

    private val scope = CoroutineScope(Dispatchers.Main)
    private val _state = MutableStateFlow(ServerState())
    val state: StateFlow<ServerState> = _state.asStateFlow()

    @Synchronized
    fun init(context: Context) {
        if (server != null) return
        val appCtx = context.applicationContext
        val secStore = SecureStore.open(appCtx)
        val sStore = LocalShareStore(appCtx)
        val ident = ServerCertificate.getOrCreate(secStore.deviceName)

        secureStore = secStore
        shareStore = sStore
        identity = ident

        val srv = LanLinkServer(
            context = appCtx,
            deviceId = secStore.clientId(),
            deviceNameProvider = { secStore.deviceName },
            identity = ident,
            shareStore = sStore,
        )
        val adv = ServerAdvertiser(appCtx)

        server = srv
        advertiser = adv

        val port = srv.start()
        adv.register(secStore.clientId(), secStore.deviceName, port, ident.fingerprint)

        registerNetworkMonitor(appCtx)
        refreshState()
    }

    private fun registerNetworkMonitor(context: Context) {
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            connectivityManager = cm ?: return

            val callback = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    Log.i(TAG, "Network available, updating IP & re-advertising")
                    onNetworkChanged()
                }

                override fun onLost(network: Network) {
                    Log.i(TAG, "Network lost, refreshing network state")
                    onNetworkChanged()
                }

                override fun onLinkPropertiesChanged(network: Network, linkProperties: LinkProperties) {
                    Log.i(TAG, "Link properties changed, updating IP")
                    onNetworkChanged()
                }

                override fun onCapabilitiesChanged(network: Network, networkCapabilities: NetworkCapabilities) {
                    onNetworkChanged()
                }
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                cm.registerDefaultNetworkCallback(callback)
            } else {
                val request = NetworkRequest.Builder()
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    .build()
                cm.registerNetworkCallback(request, callback)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not register network callback", e)
        }
    }

    fun onNetworkChanged() {
        scope.launch {
            delay(500) // Debounce network flap
            val oldIp = _state.value.ipAddress
            val newIp = getLocalIp()
            val secStore = secureStore ?: return@launch
            val srv = server ?: return@launch
            val adv = advertiser ?: return@launch
            val ident = identity ?: return@launch

            if (newIp != oldIp && newIp != "127.0.0.1") {
                Log.i(TAG, "IP changed from $oldIp to $newIp. Re-registering mDNS advertisement.")
                adv.register(secStore.clientId(), secStore.deviceName, srv.actualPort, ident.fingerprint)
            }
            refreshState()
        }
    }

    fun getLocalIp(): String {
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces() ?: return "127.0.0.1"
            for (nif in interfaces) {
                if (nif.isLoopback || !nif.isUp) continue
                val addresses = nif.inetAddresses
                for (addr in addresses) {
                    if (!addr.isLoopbackAddress && addr is Inet4Address) {
                        val host = addr.hostAddress ?: continue
                        if (host.startsWith("192.168.") || host.startsWith("10.") || host.startsWith("172.")) {
                            return host
                        }
                    }
                }
            }
        } catch (_: Exception) {}
        return "127.0.0.1"
    }

    fun refreshState() {
        val srv = server ?: return
        val secStore = secureStore ?: return
        val sStore = shareStore ?: return
        val ident = identity ?: return

        val ip = getLocalIp()
        val port = srv.actualPort
        val isArmed = srv.isArmed.value
        val code = _state.value.pairingCode

        val invite = if (isArmed && code.isNotEmpty()) {
            Invite(
                host = ip,
                port = port,
                code = code,
                deviceId = secStore.clientId(),
                name = secStore.deviceName,
                fingerprint = ident.fingerprint,
                scheme = "https",
            ).toUrl()
        } else {
            ""
        }

        _state.value = ServerState(
            isRunning = true,
            ipAddress = ip,
            networkName = if (ip.startsWith("192.168.") || ip.startsWith("10.") || ip.startsWith("172.")) "Local Wi-Fi" else "Offline",
            port = port,
            url = "https://$ip:$port",
            deviceName = secStore.deviceName,
            deviceId = secStore.clientId(),
            fingerprint = ident.fingerprint,
            shortFingerprint = ident.shortFingerprint,
            isArmed = isArmed,
            pairingCode = code,
            inviteUrl = invite,
            shares = sStore.all(),
        )
    }

    fun armPairing(): String {
        val srv = server ?: return ""
        val code = srv.armPairing()
        _state.value = _state.value.copy(isArmed = true, pairingCode = code)
        refreshState()
        return code
    }

    fun disarmPairing() {
        server?.disarmPairing()
        _state.value = _state.value.copy(isArmed = false, pairingCode = "")
        refreshState()
    }

    fun addShare(name: String, treeUri: Uri, writable: Boolean = true, removable: Boolean = true): LocalShare {
        val sStore = shareStore ?: throw IllegalStateException("Not initialized")
        val share = sStore.add(name, treeUri, writable, removable)
        refreshState()
        return share
    }

    fun removeShare(id: String) {
        shareStore?.remove(id)
        refreshState()
    }

    fun onDeviceNameChanged(newName: String) {
        val secStore = secureStore ?: return
        val srv = server ?: return
        val adv = advertiser ?: return
        val ident = identity ?: return

        secStore.deviceName = newName
        adv.register(secStore.clientId(), newName, srv.actualPort, ident.fingerprint)
        refreshState()
    }
}
