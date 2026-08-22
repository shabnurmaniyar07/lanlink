package link.lan.android.vm

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import link.lan.android.data.SecureStore
import link.lan.android.net.Discovery
import android.util.Log
import link.lan.app.DeviceRow
import link.lan.app.DeviceStanding
import link.lan.app.KnownDevice
import link.lan.app.Reconnection
import link.lan.app.SeenDevice
import link.lan.app.mergeDevices
import link.lan.app.Relocation
import link.lan.app.reconnect
import link.lan.app.relocate

/**
 * The devices screen's state.
 *
 * It owns no rules. What a device list *is* — which rows appear, in what order,
 * which are ready and which are impostors — is [mergeDevices] in android/logic,
 * which is tested against a live node. This class subscribes to discovery,
 * reads the store, and puts the two through that function.
 */
data class DevicesUiState(
    val rows: List<DeviceRow> = emptyList(),
    val discovering: Boolean = false,
    val message: String? = null,
    val busyDeviceId: String? = null,
)

class DevicesViewModel(application: Application) : AndroidViewModel(application) {

    private companion object {
        const val TAG = "LanLinkDiscovery"
    }

    private val store = SecureStore.open(application)
    private val discovery = Discovery(application)

    private val seen = MutableStateFlow<List<SeenDevice>>(emptyList())
    private val _state = MutableStateFlow(DevicesUiState())
    val state: StateFlow<DevicesUiState> = _state.asStateFlow()

    val clientId: String = store.clientId()
    val deviceName: String get() = store.deviceName

    init {
        refreshRows()
        watchDiscovery()
    }

    private fun watchDiscovery() {
        viewModelScope.launch {
            _state.value = _state.value.copy(discovering = true)
            discovery.devices()
                .catch { error ->
                    _state.value = _state.value.copy(
                        discovering = false,
                        message = "Discovery is not available on this network: ${error.message}",
                    )
                }
                .collect { devices ->
                    seen.value = devices
                    rememberAddresses(devices)
                    refreshRows()
                }
        }
    }

    private fun rememberAddresses(devices: List<SeenDevice>) {
        val known = store.load()
        var changed = false
        for (device in devices) {
            val existing = known.find(device.id) ?: continue
            if (existing.host != device.host || existing.port != device.port || (device.name.isNotBlank() && existing.name != device.name)) {
                known.seen(device.id, device.host, device.port, System.currentTimeMillis(), device.name)
                changed = true
            }
        }
        if (changed) store.save(known)
    }

    fun refreshRows() {
        val visibleSeen = seen.value.filter { it.id != clientId }
        val rows = mergeDevices(store.load().all(), visibleSeen)
        for (row in rows) {
            when (row.standing) {
                DeviceStanding.READY ->
                    Log.i(TAG, "paired device matched on the network: ${row.name} at ${row.address}")

                DeviceStanding.IMPOSTOR ->
                    Log.w(
                        TAG,
                        "MISMATCH: ${row.name} at ${row.address} advertises a fingerprint that " +
                            "does not match the stored pin — refusing to connect",
                    )

                else -> Unit
            }
        }
        _state.value = _state.value.copy(rows = rows)
    }

    /** Store what pairing produced, then show it. */
    fun remember(device: KnownDevice) {
        store.remember(device)
        refreshRows()
        _state.value = _state.value.copy(message = "Paired with ${device.name}.")
    }

    /**
     * Drop a device here and, if it can be reached, on the PC as well.
     *
     * Local first: a phone that cannot reach the PC must still be able to
     * forget it, and a token left on the PC is revoked from the PC's own
     * settings.
     */
    fun forget(row: DeviceRow) {
        val device = row.known ?: return
        store.forget(device.id)
        refreshRows()
        _state.value = _state.value.copy(message = "Forgot ${device.name}.")

        viewModelScope.launch(Dispatchers.IO) {
            runCatching {
                when (val outcome = reconnect(device)) {
                    is Reconnection.Ready -> outcome.client.unpair(clientIdFor(device))
                    else -> false
                }
            }
        }
    }

    /** Ask a paired device whether it is really there, and say what came back. */
    fun check(row: DeviceRow) {
        val device = row.known ?: return
        _state.value = _state.value.copy(busyDeviceId = device.id, message = null)
        viewModelScope.launch {
            val outcome = withContext(Dispatchers.IO) { reconnect(device) }
            val message = when (outcome) {
                is Reconnection.Ready -> {
                    val shares = withContext(Dispatchers.IO) {
                        runCatching { outcome.client.shares().size }.getOrNull()
                    }
                    if (shares == null) {
                        "${device.name} answered, but refused the token. Pair again."
                    } else {
                        "${device.name} is reachable — $shares shared folder(s)."
                    }
                }

                is Reconnection.Changed -> outcome.message
                is Reconnection.Unreachable -> "${device.name} did not answer: ${outcome.reason}"
            }
            _state.value = _state.value.copy(busyDeviceId = null, message = message)
        }
    }

    fun clearMessage() {
        _state.value = _state.value.copy(message = null)
    }

    fun rename(name: String) {
        store.deviceName = name
        refreshRows()
    }

    var allowInsecureInvites: Boolean
        get() = store.allowInsecureInvites
        set(value) {
            store.allowInsecureInvites = value
        }

    fun storedDevices(): List<KnownDevice> = store.load().all()

    fun secureStore(): SecureStore = store

    /**
     * A paired device that moved. The pin still decides: [relocate] refuses any
     * host presenting a different certificate, so this can follow a device
     * across networks but never point the stored token somewhere new.
     */
    fun updateAddress(device: KnownDevice, host: String, port: Int, onResult: (String) -> Unit) {
        viewModelScope.launch {
            val outcome = withContext(Dispatchers.IO) { relocate(device, host, port) }
            val message = when (outcome) {
                is Relocation.Moved -> {
                    store.remember(outcome.device)
                    refreshRows()
                    Log.i(TAG, "${device.name} moved to $host:$port")
                    "${device.name} is now at $host:$port."
                }

                is Relocation.WrongDevice -> {
                    Log.w(TAG, "refused to move ${device.name}: the certificate did not match")
                    outcome.message
                }

                is Relocation.Unreachable -> outcome.message
            }
            onResult(message)
        }
    }

    private fun clientIdFor(@Suppress("UNUSED_PARAMETER") device: KnownDevice): String = clientId
}
