package link.lan.android.ui

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
import androidx.compose.material.icons.filled.Backup
import androidx.compose.material.icons.filled.Cast
import androidx.compose.material.icons.filled.ContentPaste
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Mouse
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.WifiTethering
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    clientId: String,
    pairedCount: Int,
    allowInsecureInvites: Boolean,
    downloadFolder: String?,
    clipboardSyncEnabled: Boolean = true,
    cameraBackupEnabled: Boolean = false,
    hotspotModeEnabled: Boolean = true,
    onAllowInsecureChanged: (Boolean) -> Unit,
    onChooseDownloadFolder: () -> Unit,
    onToggleClipboardSync: (Boolean) -> Unit = {},
    onToggleCameraBackup: (Boolean) -> Unit = {},
    onToggleHotspotMode: (Boolean) -> Unit = {},
    onOpenTrackpad: () -> Unit = {},
    onOpenScreenShare: () -> Unit = {},
    onBack: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings", fontWeight = FontWeight.Bold) },
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
            // Downloads Section
            Text("Downloads & Storage", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.Download, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.width(10.dp))
                        Text("Download Destination", fontWeight = FontWeight.SemiBold)
                    }
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = downloadFolder?.let { "Files from laptops will be saved to:\n$it" }
                            ?: "No custom folder chosen. Files will be saved when a folder is selected.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(onClick = onChooseDownloadFolder) {
                        Text(if (downloadFolder == null) "Select Download Folder" else "Change Download Folder")
                    }
                }
            }

            SectionBreak()

            // Advanced Cross-Device Ecosystem Features
            Text("Cross-Device Ecosystem", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))

            FeatureToggleCard(
                icon = Icons.Filled.ContentPaste,
                iconColor = Color(0xFF2196F3),
                title = "Seamless Clipboard Sync",
                description = "Automatically copy and paste text, links, and code snippets between your phone and laptop in real time.",
                checked = clipboardSyncEnabled,
                onCheckedChange = onToggleClipboardSync,
            )

            Spacer(Modifier.height(10.dp))

            FeatureToggleCard(
                icon = Icons.Filled.Backup,
                iconColor = Color(0xFF4CAF50),
                title = "Camera Auto-Backup",
                description = "Automatically back up newly taken photos & videos from DCIM/Camera to your laptop when both devices are connected.",
                checked = cameraBackupEnabled,
                onCheckedChange = onToggleCameraBackup,
            )

            Spacer(Modifier.height(10.dp))

            FeatureToggleCard(
                icon = Icons.Filled.WifiTethering,
                iconColor = Color(0xFFFF9800),
                title = "Portable Hotspot / Direct Mode",
                description = "Enables zero-configuration direct sharing when outdoors or travelling using phone's personal hotspot.",
                checked = hotspotModeEnabled,
                onCheckedChange = onToggleHotspotMode,
            )

            Spacer(Modifier.height(10.dp))

            // Quick Tools Card: Trackpad & Screen Share
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Text("Interactive Remote Tools", fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(12.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        OutlinedButton(
                            onClick = onOpenTrackpad,
                            modifier = Modifier.weight(1f),
                        ) {
                            Icon(Icons.Filled.Mouse, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Virtual Trackpad")
                        }
                        OutlinedButton(
                            onClick = onOpenScreenShare,
                            modifier = Modifier.weight(1f),
                        ) {
                            Icon(Icons.Filled.Cast, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Screen Mirror")
                        }
                    }
                }
            }

            SectionBreak()

            // Security & Privacy Section
            Text("Security & Privacy", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("Allow unencrypted invites", fontWeight = FontWeight.SemiBold)
                            Text(
                                "Off by default. Keep off unless pairing with an older test environment without TLS.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Switch(checked = allowInsecureInvites, onCheckedChange = onAllowInsecureChanged)
                    }
                    Spacer(Modifier.height(12.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(12.dp))
                    Text(
                        text = "Device ID: $clientId\nPaired Devices: $pairedCount\nHardware TLS Certificate Pinning: Enforced",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Spacer(Modifier.height(20.dp))
        }
    }
}

@Composable
private fun FeatureToggleCard(
    icon: ImageVector,
    iconColor: Color,
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(iconColor.copy(alpha = 0.15f)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, contentDescription = null, tint = iconColor, modifier = Modifier.size(22.dp))
            }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
                Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.width(10.dp))
            Switch(checked = checked, onCheckedChange = onCheckedChange)
        }
    }
}

@Composable
private fun SectionBreak() {
    Spacer(Modifier.height(20.dp))
    HorizontalDivider()
    Spacer(Modifier.height(20.dp))
}
