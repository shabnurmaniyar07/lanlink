package link.lan.android.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.QrCode
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import link.lan.android.server.LocalShare
import link.lan.android.server.ServerState

fun generateQrBitmap(content: String, sizePx: Int = 512): Bitmap? {
    if (content.isBlank()) return null
    return try {
        val writer = QRCodeWriter()
        val bitMatrix = writer.encode(content, BarcodeFormat.QR_CODE, sizePx, sizePx)
        val width = bitMatrix.width
        val height = bitMatrix.height
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.RGB_565)
        for (x in 0 until width) {
            for (y in 0 until height) {
                bitmap.setPixel(x, y, if (bitMatrix.get(x, y)) android.graphics.Color.BLACK else android.graphics.Color.WHITE)
            }
        }
        bitmap
    } catch (_: Exception) {
        null
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MyDeviceScreen(
    serverState: ServerState,
    onDeviceNameChanged: (String) -> Unit,
    onAddSharedFolder: () -> Unit,
    onRemoveShare: (String) -> Unit,
    onArmPairing: () -> Unit,
    onDisarmPairing: () -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    var editingName by remember(serverState.deviceName) { mutableStateOf(serverState.deviceName) }

    fun copyToClipboard(label: String, text: String) {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        clipboard?.setPrimaryClip(ClipData.newPlainText(label, text))
        Toast.makeText(context, "$label copied to clipboard", Toast.LENGTH_SHORT).show()
    }

    val qrBitmap = remember(serverState.inviteUrl) {
        if (serverState.inviteUrl.isNotEmpty()) {
            generateQrBitmap(serverState.inviteUrl, 512)
        } else {
            null
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("My Device", fontWeight = FontWeight.SemiBold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            // Header / How other LanLink devices see this phone
            Text(
                "How other LanLink devices see this device on the local network.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))

            // Device Info Card (Same as Laptop My Device form)
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    // Name row
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Name:", fontWeight = FontWeight.SemiBold, modifier = Modifier.width(90.dp))
                        Text(serverState.deviceName, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    }

                    // Status row
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Status:", fontWeight = FontWeight.SemiBold, modifier = Modifier.width(90.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                Icons.Filled.Check,
                                contentDescription = "Online",
                                tint = Color(0xFF4CAF50),
                                modifier = Modifier.size(14.dp),
                            )
                            Spacer(Modifier.width(4.dp))
                            Text("Online (Listening on port ${serverState.port})", color = Color(0xFF4CAF50), style = MaterialTheme.typography.bodyMedium)
                        }
                    }

                    // Address / URL row (with copy button)
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp)
                            .clickable { copyToClipboard("Device URL", serverState.url) },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Address:", fontWeight = FontWeight.SemiBold, modifier = Modifier.width(90.dp))
                        Text(
                            serverState.url,
                            color = MaterialTheme.colorScheme.primary,
                            fontWeight = FontWeight.Medium,
                            modifier = Modifier.weight(1f),
                        )
                        Icon(
                            Icons.Filled.ContentCopy,
                            contentDescription = "Copy URL",
                            modifier = Modifier.size(16.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                    }

                    // Device ID row
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp)
                            .clickable { copyToClipboard("Device ID", serverState.deviceId) },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Device ID:", fontWeight = FontWeight.SemiBold, modifier = Modifier.width(90.dp))
                        Text(
                            serverState.deviceId,
                            style = MaterialTheme.typography.bodySmall,
                            fontFamily = FontFamily.Monospace,
                            modifier = Modifier.weight(1f),
                        )
                        Icon(
                            Icons.Filled.ContentCopy,
                            contentDescription = "Copy ID",
                            modifier = Modifier.size(16.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    // Certificate Fingerprint row
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp)
                            .clickable { copyToClipboard("Certificate Fingerprint", serverState.fingerprint) },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Certificate:", fontWeight = FontWeight.SemiBold, modifier = Modifier.width(90.dp))
                        Text(
                            serverState.shortFingerprint,
                            style = MaterialTheme.typography.bodySmall,
                            fontFamily = FontFamily.Monospace,
                            modifier = Modifier.weight(1f),
                        )
                        Icon(
                            Icons.Filled.ContentCopy,
                            contentDescription = "Copy Fingerprint",
                            modifier = Modifier.size(16.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            Spacer(Modifier.height(20.dp))

            // Pairing Section (Matching Desktop)
            Text("Pairing", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))
            Text(
                if (serverState.isArmed) "Pairing is armed: this device will accept one new connection during the next 180s."
                else "Pairing is off. Press 'Allow a device to pair' to let a laptop or phone connect.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))

            if (serverState.isArmed && serverState.pairingCode.isNotEmpty()) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
                ) {
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text("Pairing code:", style = MaterialTheme.typography.labelMedium)
                        Spacer(Modifier.height(4.dp))
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.clickable { copyToClipboard("Pairing code", serverState.pairingCode) },
                        ) {
                            Text(
                                text = serverState.pairingCode.chunked(4).joinToString(" "),
                                fontSize = 32.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace,
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                            )
                            Spacer(Modifier.width(8.dp))
                            Icon(
                                Icons.Filled.ContentCopy,
                                contentDescription = "Copy code",
                                modifier = Modifier.size(20.dp),
                                tint = MaterialTheme.colorScheme.onPrimaryContainer,
                            )
                        }

                        Spacer(Modifier.height(16.dp))

                        // Action Buttons: Stop Pairing & Copy Invite Link
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            OutlinedButton(
                                onClick = onDisarmPairing,
                                modifier = Modifier.weight(1f),
                            ) {
                                Icon(Icons.Filled.Lock, contentDescription = null, modifier = Modifier.size(16.dp))
                                Spacer(Modifier.width(4.dp))
                                Text("Stop pairing")
                            }

                            Button(
                                onClick = { copyToClipboard("Invite link", serverState.inviteUrl) },
                                modifier = Modifier.weight(1f),
                            ) {
                                Icon(Icons.Filled.ContentCopy, contentDescription = null, modifier = Modifier.size(16.dp))
                                Spacer(Modifier.width(4.dp))
                                Text("Copy invite link")
                            }
                        }

                        Spacer(Modifier.height(16.dp))

                        // QR Code to scan
                        if (qrBitmap != null) {
                            Box(
                                modifier = Modifier
                                    .size(220.dp)
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(Color.White)
                                    .padding(10.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                Image(
                                    bitmap = qrBitmap.asImageBitmap(),
                                    contentDescription = "QR Code Invite",
                                    modifier = Modifier.fillMaxSize(),
                                )
                            }
                            Spacer(Modifier.height(10.dp))
                            Text(
                                "Scan this with LanLink on a laptop or another phone, or press Copy invite link and paste it into the other device's Devices page.",
                                style = MaterialTheme.typography.bodySmall,
                                textAlign = TextAlign.Center,
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                            )
                        }
                    }
                }
            } else {
                Button(
                    onClick = onArmPairing,
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                ) {
                    Icon(Icons.Filled.LockOpen, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Allow a device to pair (Show Code & QR)")
                }
            }

            Spacer(Modifier.height(24.dp))
            HorizontalDivider()
            Spacer(Modifier.height(20.dp))

            // Shared Folders Section
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Shared folders on this phone", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                IconButton(onClick = onAddSharedFolder) {
                    Icon(Icons.Filled.Add, contentDescription = "Add folder")
                }
            }
            Text(
                "These folders can be browsed and downloaded from your paired laptops.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(10.dp))

            if (serverState.shares.isEmpty()) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                ) {
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text("No shared folders added yet.", style = MaterialTheme.typography.bodyMedium)
                        Spacer(Modifier.height(8.dp))
                        Button(onClick = onAddSharedFolder) {
                            Text("+ Add Shared Folder")
                        }
                    }
                }
            } else {
                serverState.shares.forEach { share ->
                    Card(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth().padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.weight(1f)) {
                                Icon(Icons.Filled.Folder, contentDescription = "Folder", tint = MaterialTheme.colorScheme.primary)
                                Spacer(Modifier.width(10.dp))
                                Column {
                                    Text(share.name, fontWeight = FontWeight.SemiBold)
                                    Text(if (share.writable) "Read & Write" else "Read Only", style = MaterialTheme.typography.bodySmall)
                                }
                            }
                            IconButton(onClick = { onRemoveShare(share.id) }) {
                                Icon(Icons.Filled.Delete, contentDescription = "Remove", tint = MaterialTheme.colorScheme.error)
                            }
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = onAddSharedFolder, modifier = Modifier.fillMaxWidth()) {
                    Text("+ Add Another Shared Folder")
                }
            }

            Spacer(Modifier.height(24.dp))
            HorizontalDivider()
            Spacer(Modifier.height(20.dp))

            // Rename Section
            Text("Device Name", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = editingName,
                onValueChange = { editingName = it },
                label = { Text("Name shown to other devices") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            Button(
                onClick = { onDeviceNameChanged(editingName) },
                enabled = editingName.isNotBlank() && editingName != serverState.deviceName,
            ) {
                Text("Save Name")
            }
        }
    }
}
