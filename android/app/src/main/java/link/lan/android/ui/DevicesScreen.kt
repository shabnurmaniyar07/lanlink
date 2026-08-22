package link.lan.android.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.DeleteOutline
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SwapVert
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import link.lan.android.server.ServerState
import link.lan.app.DeviceRow
import link.lan.app.DeviceStanding

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DevicesScreen(
    rows: List<DeviceRow>,
    discovering: Boolean,
    busyDeviceId: String?,
    message: String?,
    serverState: ServerState? = null,
    onRefresh: () -> Unit = {},
    onAddDevice: () -> Unit,
    onMyDevice: () -> Unit = {},
    onSettings: () -> Unit,
    onOpen: (DeviceRow) -> Unit,
    onPair: (DeviceRow) -> Unit,
    onTransfers: () -> Unit,
    onUpdateAddress: (DeviceRow) -> Unit,
    onCheck: (DeviceRow) -> Unit,
    onForget: (DeviceRow) -> Unit,
    onMessageShown: () -> Unit,
) {
    val snackbars = remember { SnackbarHostState() }

    LaunchedEffect(message) {
        if (message != null) {
            snackbars.showSnackbar(message)
            onMessageShown()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("LanLink", fontWeight = FontWeight.Bold)
                        Text(
                            text = if (serverState != null && serverState.ipAddress.isNotEmpty())
                                "IP: ${serverState.ipAddress} • Port ${serverState.port}"
                            else "Scanning network...",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = onRefresh) {
                        if (discovering) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.primary,
                            )
                        } else {
                            Icon(Icons.Filled.Refresh, contentDescription = "Rescan network")
                        }
                    }
                    IconButton(onClick = onMyDevice) {
                        Icon(Icons.Filled.Folder, contentDescription = "My Device")
                    }
                    IconButton(onClick = onTransfers) {
                        Icon(Icons.Filled.SwapVert, contentDescription = "Transfers")
                    }
                    IconButton(onClick = onSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = "Settings")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = onAddDevice,
                icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                text = { Text("Add device") },
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            )
        },
        snackbarHost = { SnackbarHost(snackbars) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            // Modern Network Status Banner
            if (serverState != null) {
                NetworkStatusBanner(serverState, onMyDevice, onRefresh)
            }

            if (rows.isEmpty()) {
                EmptyDevices(discovering, onAddDevice, Modifier.weight(1f))
            } else {
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    val paired = rows.filter { it.isPaired }
                    val strangers = rows.filter { !it.isPaired }

                    if (paired.isNotEmpty()) {
                        item {
                            SectionHeader(title = "PAIRED DEVICES", count = paired.size)
                        }
                        items(paired, key = { it.id }) { row ->
                            AdvancedDeviceCard(
                                row = row,
                                busy = busyDeviceId == row.id,
                                onOpen = onOpen,
                                onPair = onPair,
                                onUpdateAddress = onUpdateAddress,
                                onCheck = onCheck,
                                onForget = onForget,
                            )
                        }
                    }

                    if (strangers.isNotEmpty()) {
                        item {
                            Spacer(Modifier.height(8.dp))
                            SectionHeader(title = "FOUND ON NETWORK", count = strangers.size)
                        }
                        items(strangers, key = { it.id }) { row ->
                            AdvancedDeviceCard(
                                row = row,
                                busy = busyDeviceId == row.id,
                                onOpen = onOpen,
                                onPair = onPair,
                                onUpdateAddress = onUpdateAddress,
                                onCheck = onCheck,
                                onForget = onForget,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun NetworkStatusBanner(
    serverState: ServerState,
    onMyDevice: () -> Unit,
    onRefresh: () -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 6.dp)
            .clickable { onMyDevice() },
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.weight(1f)) {
                Box(
                    modifier = Modifier
                        .size(32.dp)
                        .clip(CircleShape)
                        .background(Color(0xFF2E7D32).copy(alpha = 0.15f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Filled.Wifi,
                        contentDescription = null,
                        tint = Color(0xFF2E7D32),
                        modifier = Modifier.size(18.dp),
                    )
                }
                Spacer(Modifier.width(10.dp))
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .clip(CircleShape)
                                .background(Color(0xFF4CAF50)),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = "Sharing Active • ${serverState.deviceName}",
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                    Text(
                        text = "Auto-reconnects on IP change: ${serverState.url}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }

            Icon(
                Icons.AutoMirrored.Filled.ArrowForward,
                contentDescription = "My Device Details",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}

@Composable
private fun SectionHeader(title: String, count: Int) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary,
            letterSpacing = 1.sp,
        )
        Surface(
            color = MaterialTheme.colorScheme.primaryContainer,
            shape = CircleShape,
        ) {
            Text(
                text = count.toString(),
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            )
        }
    }
}

@Composable
private fun AdvancedDeviceCard(
    row: DeviceRow,
    busy: Boolean,
    onOpen: (DeviceRow) -> Unit,
    onPair: (DeviceRow) -> Unit,
    onUpdateAddress: (DeviceRow) -> Unit,
    onCheck: (DeviceRow) -> Unit,
    onForget: (DeviceRow) -> Unit,
) {
    val isReady = row.standing == DeviceStanding.READY
    val isImpostor = row.standing == DeviceStanding.IMPOSTOR

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (row.canOpen) Modifier.clickable { onOpen(row) } else Modifier),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = when {
                isImpostor -> MaterialTheme.colorScheme.errorContainer
                isReady -> MaterialTheme.colorScheme.surfaceVariant
                else -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f)
            },
        ),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Device Icon Frame
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(
                            if (isReady) MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
                            else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.08f),
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    val isPhone = row.name.contains("Phone", ignoreCase = true) ||
                        row.name.contains("Android", ignoreCase = true) ||
                        row.name.contains("OnePlus", ignoreCase = true) ||
                        row.name.contains("Samsung", ignoreCase = true) ||
                        row.name.contains("Pixel", ignoreCase = true)

                    Icon(
                        if (isPhone) Icons.Filled.PhoneAndroid else Icons.Filled.Computer,
                        contentDescription = null,
                        tint = if (isReady) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(24.dp),
                    )
                }

                Spacer(Modifier.width(12.dp))

                Column(Modifier.weight(1f)) {
                    Text(
                        text = row.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = row.address,
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                // Status Badge Pill
                StatusBadge(standing = row.standing)
            }

            Spacer(Modifier.height(10.dp))

            // Subtitle / Description
            Text(
                text = describe(row.standing),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Spacer(Modifier.height(12.dp))

            // Action Buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                when (row.standing) {
                    DeviceStanding.NEW -> {
                        Button(
                            onClick = { onPair(row) },
                            modifier = Modifier.weight(1f),
                        ) {
                            Icon(Icons.Filled.Link, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Pair Device")
                        }
                    }

                    DeviceStanding.IMPOSTOR -> {
                        Button(
                            onClick = { onPair(row) },
                            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                            modifier = Modifier.weight(1f),
                        ) {
                            Icon(Icons.Filled.Security, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Pair Again")
                        }
                        OutlinedButton(onClick = { onForget(row) }) {
                            Text("Forget")
                        }
                    }

                    DeviceStanding.READY -> {
                        Button(
                            onClick = { onOpen(row) },
                            modifier = Modifier.weight(1f),
                        ) {
                            Icon(Icons.Filled.Folder, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Open")
                        }
                        OutlinedButton(
                            onClick = { onCheck(row) },
                            enabled = !busy,
                        ) {
                            if (busy) {
                                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                            } else {
                                Text("Check")
                            }
                        }
                        IconButton(onClick = { onForget(row) }) {
                            Icon(Icons.Filled.DeleteOutline, contentDescription = "Forget", tint = MaterialTheme.colorScheme.error)
                        }
                    }

                    else -> {
                        Button(
                            onClick = { onCheck(row) },
                            enabled = !busy,
                            modifier = Modifier.weight(1f),
                        ) {
                            if (busy) {
                                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                            } else {
                                Text("Check Connection")
                            }
                        }
                        OutlinedButton(onClick = { onUpdateAddress(row) }) {
                            Icon(Icons.Filled.Edit, contentDescription = null, modifier = Modifier.size(14.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Address")
                        }
                        IconButton(onClick = { onForget(row) }) {
                            Icon(Icons.Filled.DeleteOutline, contentDescription = "Forget", tint = MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusBadge(standing: DeviceStanding) {
    val (bgColor, textColor, label) = when (standing) {
        DeviceStanding.READY -> Triple(Color(0xFFE8F5E9), Color(0xFF2E7D32), "Online")
        DeviceStanding.AWAY -> Triple(MaterialTheme.colorScheme.surfaceVariant, MaterialTheme.colorScheme.onSurfaceVariant, "Offline")
        DeviceStanding.NEW -> Triple(MaterialTheme.colorScheme.primaryContainer, MaterialTheme.colorScheme.onPrimaryContainer, "Discovered")
        DeviceStanding.IMPOSTOR -> Triple(MaterialTheme.colorScheme.errorContainer, MaterialTheme.colorScheme.onErrorContainer, "Mismatch")
    }

    Surface(
        color = bgColor,
        shape = RoundedCornerShape(8.dp),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(6.dp)
                    .clip(CircleShape)
                    .background(textColor),
            )
            Spacer(Modifier.width(4.dp))
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = textColor,
            )
        }
    }
}

private fun describe(standing: DeviceStanding): String = when (standing) {
    DeviceStanding.READY -> "Paired and connected over local network."
    DeviceStanding.AWAY -> "Paired, but not detected on current Wi-Fi. It will auto-connect once online."
    DeviceStanding.NEW -> "Discovered on network. Tap Pair to connect."
    DeviceStanding.IMPOSTOR -> "Certificate changed. Please re-pair."
}

@Composable
private fun EmptyDevices(discovering: Boolean, onAddDevice: () -> Unit, modifier: Modifier) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier
                .size(72.dp)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.primaryContainer),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Filled.Wifi,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onPrimaryContainer,
                modifier = Modifier.size(36.dp),
            )
        }
        Spacer(Modifier.height(16.dp))
        Text("Looking for devices", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        Text(
            text = if (discovering) {
                "Scanning your local Wi-Fi. Make sure your laptop is open on the same network."
            } else {
                "Open LanLink on your laptop, then add it here."
            },
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        Spacer(Modifier.height(24.dp))
        Button(
            onClick = onAddDevice,
            modifier = Modifier.height(48.dp),
        ) {
            Icon(Icons.Filled.Add, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("Add a device manually")
        }
    }
}
