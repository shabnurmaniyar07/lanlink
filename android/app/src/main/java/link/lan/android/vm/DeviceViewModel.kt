package link.lan.android.vm

import android.app.Application
import android.net.Uri
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import link.lan.android.data.SecureStore
import link.lan.android.data.TransferCentre
import link.lan.android.net.Session
import link.lan.app.BrowseState
import link.lan.app.Crumb
import link.lan.app.KnownDevice
import link.lan.app.Sorting
import link.lan.app.Standing
import link.lan.app.failureOf
import link.lan.core.Entry
import link.lan.core.Share

/**
 * One device: connecting to it, and looking around inside it.
 *
 * Where a screen is, what it may draw and what it may offer is [BrowseState] in
 * android/logic — including the rule that a path from the other end is checked
 * before it is used. This class fetches listings and hands them over; it does
 * not decide what they mean.
 */
data class DeviceUiState(
    val device: KnownDevice? = null,
    val standing: Standing = Standing.OFFLINE,
    val message: String = "",
    val connecting: Boolean = false,
    val browse: BrowseState = BrowseState(),
    val shares: List<Share> = emptyList(),
    val notice: String? = null,
) {
    val connected: Boolean get() = standing == Standing.CONNECTED
    val isImpostor: Boolean get() = standing == Standing.IMPOSTOR
    val worthRetrying: Boolean get() = standing == Standing.OFFLINE || standing == Standing.PROBLEM

    /** Uploading needs somewhere to put it, and permission to put it there. */
    val canUpload: Boolean get() = connected && browse.share != null && browse.canWrite
}

class DeviceViewModel(application: Application) : AndroidViewModel(application) {

    private val store = SecureStore.open(application)

    private val _state = MutableStateFlow(DeviceUiState())
    val state: StateFlow<DeviceUiState> = _state.asStateFlow()

    private var session: Session? = null

    /** Open a device: connect, verify, authenticate, then list its shares. */
    fun open(device: KnownDevice) {
        _state.value = DeviceUiState(device = device, connecting = true, message = "Connecting…")
        viewModelScope.launch {
            val opened = withContext(Dispatchers.IO) { Session.open(device) }
            session = opened
            _state.value = _state.value.copy(
                standing = opened.standing,
                message = opened.connection.message,
                connecting = false,
                shares = opened.shares,
                browse = BrowseState(),
            )
            if (opened.standing != Standing.CONNECTED) {
                // Anything queued for a device we cannot reach is not going to
                // happen; failing it now beats a queue that quietly stalls.
                TransferCentre.deviceLost(device.id)
            }
        }
    }

    fun reconnect() {
        _state.value.device?.let(::open)
    }

    fun close() {
        session = null
        _state.value = DeviceUiState()
    }

    suspend fun loadThumbnail(shareId: String, path: String): android.graphics.Bitmap? = withContext(Dispatchers.IO) {
        val client = session?.client ?: return@withContext null
        val cacheKey = "$shareId:$path"
        val cached = link.lan.android.ui.ThumbnailCache.get(cacheKey)
        if (cached != null) return@withContext cached

        try {
            client.download(shareId, path, offset = 0) { _, inputStream ->
                val opts = android.graphics.BitmapFactory.Options().apply {
                    inSampleSize = 4
                }
                val bmp = android.graphics.BitmapFactory.decodeStream(inputStream, null, opts)
                if (bmp != null) {
                    link.lan.android.ui.ThumbnailCache.put(cacheKey, bmp)
                }
                bmp
            }
        } catch (_: Exception) {
            null
        }
    }

    // -------------------------------------------------------------- browsing

    fun openShare(share: Share) {
        val browse = _state.value.browse.openShare(share)
        _state.value = _state.value.copy(browse = browse)
        load(browse)
    }

    /** Named apart from open(KnownDevice): `device::openEntry` must resolve to one. */
    fun openEntry(entry: Entry) {
        val browse = _state.value.browse.into(entry)
        _state.value = _state.value.copy(browse = browse)
        if (browse.loading) load(browse)
    }

    /** True when the caller handled it; false means leave the browser. */
    fun up(): Boolean {
        val browse = _state.value.browse.up() ?: return false
        _state.value = _state.value.copy(browse = browse)
        load(browse)
        return true
    }

    fun jumpTo(crumb: Crumb) {
        val browse = _state.value.browse.jumpTo(crumb)
        if (browse == _state.value.browse) return
        _state.value = _state.value.copy(browse = browse)
        load(browse)
    }

    fun refresh() {
        val browse = _state.value.browse
        if (browse.share == null) {
            reconnect()
            return
        }
        _state.value = _state.value.copy(browse = browse.copy(loading = true))
        load(browse)
    }

    fun search(query: String) {
        _state.value = _state.value.copy(browse = _state.value.browse.searching(query))
    }

    fun sortBy(sorting: Sorting) {
        _state.value = _state.value.copy(browse = _state.value.browse.sortedBy(sorting))
    }

    fun leaveShare() {
        _state.value = _state.value.copy(browse = BrowseState())
    }

    private fun load(browse: BrowseState) {
        val share = browse.share ?: return
        val current = session ?: return
        viewModelScope.launch {
            val outcome = withContext(Dispatchers.IO) {
                runCatching { current.list(share.id, browse.path) }
            }
            val here = _state.value.browse
            // The listing that comes back may be for a folder the person has
            // already left. Dropping it is better than showing the wrong one.
            if (here.share?.id != share.id || here.path != browse.path) return@launch

            outcome
                .onSuccess { entries -> _state.value = _state.value.copy(browse = here.loaded(entries)) }
                .onFailure { error ->
                    val connection = failureOf(_state.value.device?.name.orEmpty(), error)
                    Log.w(Session.BROWSE, "listing failed", error)
                    _state.value = _state.value.copy(
                        browse = here.failed(connection.message),
                        standing = if (connection.standing == Standing.CONNECTED) {
                            _state.value.standing
                        } else {
                            connection.standing
                        },
                    )
                }
        }
    }

    // ------------------------------------------------------------- transfers

    fun download(entry: Entry) {
        val state = _state.value
        val device = state.device ?: return
        val share = state.browse.share ?: return
        if (store.downloadTree == null) {
            _state.value = state.copy(
                notice = "Choose a download folder in Settings first — LanLink asks Android " +
                    "for one folder rather than for your whole storage.",
            )
            return
        }
        TransferCentre.enqueueDownload(
            context = getApplication(),
            device = device,
            shareId = share.id,
            folder = state.browse.path,
            name = entry.name,
            size = entry.size,
        )
        _state.value = _state.value.copy(notice = "${entry.name} added to transfers.")
    }

    fun upload(uris: List<Uri>) {
        val state = _state.value
        val device = state.device ?: return
        val share = state.browse.share ?: return
        var queued = 0
        for (uri in uris) {
            val item = TransferCentre.enqueueUpload(
                context = getApplication(),
                device = device,
                shareId = share.id,
                folder = state.browse.path,
                uri = uri,
            )
            if (item != null) queued += 1
        }
        _state.value = _state.value.copy(
            notice = if (queued == 0) "Those files could not be read." else "$queued file(s) queued.",
        )
    }

    fun clearNotice() {
        _state.value = _state.value.copy(notice = null)
    }
}
