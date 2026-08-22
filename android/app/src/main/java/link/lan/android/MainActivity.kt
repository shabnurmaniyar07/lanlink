package link.lan.android

import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.core.view.WindowCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import link.lan.android.data.TransferCentre
import link.lan.android.service.TransferService
import link.lan.android.ui.AddDeviceDialog
import link.lan.android.ui.BrowseScreen
import link.lan.android.ui.DeviceInfoScreen
import link.lan.android.ui.DeviceScreen
import link.lan.android.ui.DevicesScreen
import link.lan.android.ui.LanLinkTheme
import link.lan.android.ui.MyDeviceScreen
import link.lan.android.ui.PairingDialog
import link.lan.android.ui.SettingsScreen
import link.lan.android.ui.TrackpadScreen
import link.lan.android.ui.UpdateAddressDialog
import link.lan.android.ui.TransfersScreen
import link.lan.android.vm.DeviceViewModel
import link.lan.android.vm.DevicesViewModel
import link.lan.android.vm.PairingViewModel
import link.lan.app.PairingStep
import link.lan.app.Transfer
import link.lan.app.inviteFor

import link.lan.android.server.ServerCentre

/** Where the app is. Local state rather than a navigation library: eight screens. */
private enum class Screen { DEVICES, DEVICE, BROWSE, TRANSFERS, INFORMATION, SETTINGS, MY_DEVICE, TRACKPAD }

/**
 * The only Activity.
 *
 * It owns navigation and Android's permission and picker contracts, and nothing
 * else. Connecting, browsing and transferring are the view models', and the
 * rules those follow live in android/logic, where they are tested.
 */
class MainActivity : ComponentActivity() {

    private val devices: DevicesViewModel by viewModels()
    private val pairing: PairingViewModel by viewModels()
    private val device: DeviceViewModel by viewModels()

    private var pendingInvite by mutableStateOf<String?>(null)
    private var scanned by mutableStateOf<String?>(null)

    private val chooseFolder = registerForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri: Uri? ->
        if (uri != null) {
            val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            runCatching { contentResolver.takePersistableUriPermission(uri, flags) }
                .onFailure { Log.w(TAG, "the folder grant could not be made permanent", it) }
            devices.secureStore().downloadTree = uri.toString()
            toast("Downloads will be saved there.")
        }
    }

    private val chooseShareFolder = registerForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri: Uri? ->
        if (uri != null) {
            val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            runCatching { contentResolver.takePersistableUriPermission(uri, flags) }
                .onFailure { Log.w(TAG, "the share folder grant could not be made permanent", it) }
            val name = (Uri.parse(uri.toString()).lastPathSegment ?: "Shared Folder")
                .substringAfterLast(':').substringAfterLast('/')
                .ifBlank { "Shared Folder" }
            ServerCentre.addShare(name, uri)
            toast("Shared folder '$name' added.")
        }
    }

    /**
     * Files to upload. `OpenMultipleDocuments` gives a persistable read grant
     * per file, which matters because the upload may still be queued when the
     * picker has long closed.
     */
    private val chooseUploads = registerForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris: List<Uri> ->
        if (uris.isEmpty()) return@registerForActivityResult
        for (uri in uris) {
            runCatching {
                contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
        }
        device.upload(uris)
        TransferService.ensureRunning(this)
    }

    private val scanQr = registerForActivityResult(ScanContract()) { result ->
        val text = result.contents
        if (text.isNullOrBlank()) {
            Log.i(PAIRING, "the scanner was closed without a code")
        } else {
            Log.i(PAIRING, "a code was scanned")
            scanned = text
        }
    }

    private val askNotifications = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        Log.i(TAG, "notification permission granted=$granted")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        pendingInvite = inviteFromIntent(intent)
        handleSendIntent(intent)
        link.lan.android.service.ClipboardSyncCentre.init(this)

        // Android 13+ will not show the transfer notification without this, and
        // the service is far less useful when it is silent. Asked once, here,
        // rather than in the middle of somebody's first transfer.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            askNotifications.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }

        setContent { LanLinkTheme { LanLinkApp() } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pendingInvite = inviteFromIntent(intent)
        handleSendIntent(intent)
    }

    private fun handleSendIntent(intent: Intent?) {
        val action = intent?.action ?: return
        val uris = mutableListOf<Uri>()
        if (action == Intent.ACTION_SEND) {
            val uri = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(Intent.EXTRA_STREAM) as? Uri
            }
            uri?.let { uris.add(it) }
        } else if (action == Intent.ACTION_SEND_MULTIPLE) {
            val list = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)
            }
            list?.let { uris.addAll(it) }
        }
        if (uris.isNotEmpty()) {
            for (uri in uris) {
                runCatching {
                    contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
            }
            device.upload(uris)
            TransferService.ensureRunning(this)
            toast("Sending ${uris.size} file(s)...")
        }
    }

    private fun inviteFromIntent(intent: Intent?): String? {
        val data = intent?.data ?: return null
        return if (data.scheme.equals("lanlink", ignoreCase = true)) data.toString() else null
    }

    @Composable
    private fun LanLinkApp() {
        val devicesState by devices.state.collectAsState()
        val pairingState by pairing.state.collectAsState()
        val deviceState by device.state.collectAsState()
        val transfers by TransferCentre.items.collectAsState()
        val summary by TransferCentre.summary.collectAsState()
        val serverState by ServerCentre.state.collectAsState()

        var screen by remember { mutableStateOf(Screen.DEVICES) }
        var showAddDevice by remember { mutableStateOf(false) }
        var addDeviceProblem by remember { mutableStateOf<String?>(null) }
        var relocating by remember { mutableStateOf<link.lan.app.DeviceRow?>(null) }

        LaunchedEffect(pendingInvite) {
            val invite = pendingInvite ?: return@LaunchedEffect
            pendingInvite = null
            addDeviceProblem = pairing.begin(invite)
            if (addDeviceProblem != null) showAddDevice = true
        }

        LaunchedEffect(scanned) {
            val text = scanned ?: return@LaunchedEffect
            scanned = null
            addDeviceProblem = pairing.begin(text)
            if (addDeviceProblem != null) showAddDevice = true
        }

        LaunchedEffect(pairingState.paired?.id) {
            val paired = pairingState.paired ?: return@LaunchedEffect
            devices.remember(paired)
        }

        LaunchedEffect(deviceState.notice) {
            val notice = deviceState.notice ?: return@LaunchedEffect
            toast(notice)
            device.clearNotice()
            TransferService.ensureRunning(this@MainActivity)
        }

        // Back goes up the browser first, then back through the screens.
        BackHandler(enabled = screen != Screen.DEVICES) {
            when (screen) {
                Screen.BROWSE -> if (!device.up()) {
                    if (deviceState.browse.share != null) device.leaveShare() else screen = Screen.DEVICE
                }

                Screen.DEVICE -> { device.close(); screen = Screen.DEVICES }
                Screen.MY_DEVICE, Screen.SETTINGS, Screen.TRANSFERS -> screen = Screen.DEVICES
                else -> screen = if (deviceState.device != null) Screen.DEVICE else Screen.DEVICES
            }
        }

        when (screen) {
            Screen.DEVICES -> DevicesScreen(
                rows = devicesState.rows,
                discovering = devicesState.discovering,
                busyDeviceId = devicesState.busyDeviceId,
                message = devicesState.message,
                serverState = serverState,
                onRefresh = {
                    devices.refreshRows()
                    ServerCentre.onNetworkChanged()
                },
                onAddDevice = { showAddDevice = true },
                onMyDevice = { screen = Screen.MY_DEVICE },
                onSettings = { screen = Screen.SETTINGS },
                onOpen = { row ->
                    val known = row.known ?: return@DevicesScreen
                    device.open(known)
                    screen = Screen.DEVICE
                },
                onPair = { row ->
                    val problem = pairing.begin(inviteFor(host = row.host, port = row.port, name = row.name))
                    if (problem != null) {
                        addDeviceProblem = problem
                        showAddDevice = true
                    }
                },
                onUpdateAddress = { relocating = it },
                onCheck = devices::check,
                onForget = devices::forget,
                onTransfers = { screen = Screen.TRANSFERS },
                onMessageShown = devices::clearMessage,
            )

            Screen.MY_DEVICE -> MyDeviceScreen(
                serverState = serverState,
                onDeviceNameChanged = { newName ->
                    devices.rename(newName)
                    ServerCentre.onDeviceNameChanged(newName)
                },
                onAddSharedFolder = { chooseShareFolder.launch(null) },
                onRemoveShare = { shareId -> ServerCentre.removeShare(shareId) },
                onArmPairing = { ServerCentre.armPairing() },
                onDisarmPairing = { ServerCentre.disarmPairing() },
                onBack = { screen = Screen.DEVICES },
            )

            Screen.DEVICE -> DeviceScreen(
                state = deviceState,
                onBack = { device.close(); screen = Screen.DEVICES },
                onBrowse = { device.leaveShare(); screen = Screen.BROWSE },
                onUpload = { screen = Screen.BROWSE },
                onInformation = { screen = Screen.INFORMATION },
                onTrackpad = { screen = Screen.TRACKPAD },
                onRetry = device::reconnect,
                onTransfers = { screen = Screen.TRANSFERS },
            )

            Screen.BROWSE -> BrowseScreen(
                state = deviceState,
                onBack = {
                    if (!device.up()) {
                        if (deviceState.browse.share != null) device.leaveShare() else screen = Screen.DEVICE
                    }
                },
                onOpenShare = device::openShare,
                onOpenEntry = device::openEntry,
                onCrumb = device::jumpTo,
                onDownload = device::download,
                onUpload = { chooseUploads.launch(arrayOf("*/*")) },
                onRefresh = device::refresh,
                onSearch = device::search,
                onSort = device::sortBy,
                onLoadThumbnail = device::loadThumbnail,
            )

            Screen.TRANSFERS -> TransfersScreen(
                transfers = transfers,
                summary = summary,
                speedOf = TransferCentre::speedOf,
                canOpen = { TransferCentre.uriOf(it) != null },
                onBack = { screen = if (deviceState.device != null) Screen.DEVICE else Screen.DEVICES },
                onCancel = { TransferCentre.cancel(it.id) },
                onRetry = {
                    TransferCentre.retry(this@MainActivity, it.id)
                    TransferService.ensureRunning(this@MainActivity)
                },
                onOpen = ::openTransfer,
                onClearFinished = TransferCentre::clearFinished,
            )

            Screen.INFORMATION -> DeviceInfoScreen(
                device = deviceState.device,
                standing = deviceState.standing,
                phoneName = devices.deviceName,
                phoneClientId = devices.clientId,
                onBack = { screen = Screen.DEVICE },
                onCopyFingerprint = { fingerprint ->
                    clipboard().setPrimaryClip(
                        android.content.ClipData.newPlainText("LanLink certificate", fingerprint),
                    )
                    toast("Fingerprint copied.")
                },
                onForget = {
                    devicesState.rows.firstOrNull { it.id == deviceState.device?.id }
                        ?.let(devices::forget)
                    device.close()
                    screen = Screen.DEVICES
                },
            )

            Screen.SETTINGS -> SettingsScreen(
                clientId = devices.clientId,
                pairedCount = devices.storedDevices().size,
                allowInsecureInvites = devices.allowInsecureInvites,
                downloadFolder = devices.secureStore().downloadTree?.let(::readableFolder),
                clipboardSyncEnabled = link.lan.android.service.ClipboardSyncCentre.isEnabled,
                onAllowInsecureChanged = { devices.allowInsecureInvites = it },
                onChooseDownloadFolder = { chooseFolder.launch(null) },
                onToggleClipboardSync = { link.lan.android.service.ClipboardSyncCentre.isEnabled = it },
                onOpenTrackpad = { screen = Screen.TRACKPAD },
                onBack = { screen = Screen.DEVICES },
            )

            Screen.TRACKPAD -> TrackpadScreen(
                device = deviceState.device ?: devices.storedDevices().firstOrNull(),
                onBack = { screen = if (deviceState.device != null) Screen.DEVICE else Screen.SETTINGS },
            )
        }

        relocating?.let { row ->
            val known = row.known
            if (known == null) {
                relocating = null
            } else {
                UpdateAddressDialog(
                    deviceName = row.name,
                    currentAddress = row.address,
                    onDismiss = { relocating = null },
                    onSubmit = { host, port ->
                        relocating = null
                        devices.updateAddress(known, host, port) { toast(it) }
                    },
                )
            }
        }

        if (showAddDevice) {
            AddDeviceDialog(
                initialProblem = addDeviceProblem,
                onDismiss = { showAddDevice = false; addDeviceProblem = null },
                onSubmit = { text ->
                    val problem = pairing.begin(text)
                    if (problem == null) showAddDevice = false
                    problem
                },
                onScan = {
                    showAddDevice = false
                    scanQr.launch(
                        ScanOptions()
                            .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                            .setPrompt("Point the camera at the QR code on the PC")
                            .setBeepEnabled(false)
                            .setOrientationLocked(false),
                    )
                },
                onPaste = ::clipboardText,
            )
        }

        if (pairingState.step != PairingStep.IDLE) {
            PairingDialog(
                state = pairingState,
                onConfirmFingerprint = pairing::confirmByEye,
                onSubmitCode = pairing::submit,
                onDismiss = {
                    pairing.cancel()
                    devices.refreshRows()
                },
            )
        }
    }

    /** Hand a finished download to whatever app can open it. */
    private fun openTransfer(transfer: Transfer) {
        val uri = TransferCentre.uriOf(transfer.id) ?: return
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, contentResolver.getType(uri) ?: "*/*")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        runCatching { startActivity(intent) }
            .onFailure { toast("No app on this phone can open ${transfer.name}.") }
    }

    private fun clipboard(): ClipboardManager =
        getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager

    private fun clipboardText(): String? =
        clipboard().primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.text?.toString()

    private fun toast(text: String) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show()
    }

    private fun readableFolder(uri: String): String =
        runCatching { Uri.parse(uri).lastPathSegment ?: uri }.getOrDefault(uri)

    companion object {
        private const val TAG = "LanLink"
        private const val PAIRING = "LanLinkPairing"
    }
}
