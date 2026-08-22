package link.lan.android.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import link.lan.app.KnownDevice
import link.lan.app.Standing
import link.lan.core.Pinning

/**
 * What this phone knows about a paired PC, and about itself.
 *
 * Only fields the protocol actually carries. The phone does not serve LanLink,
 * so it has no address, no certificate and no invite of its own to show — and
 * inventing a QR code for it would be a lie in the shape of a feature.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DeviceInfoScreen(
    device: KnownDevice?,
    standing: Standing,
    phoneName: String,
    phoneClientId: String,
    onBack: () -> Unit,
    onCopyFingerprint: (String) -> Unit,
    onForget: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Device information") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(20.dp),
        ) {
            if (device == null) {
                Text("No device selected.")
                return@Column
            }

            Text("The PC", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(12.dp))
            Field("Name", device.name)
            Field("Address", device.host)
            Field("Port", device.port.toString())
            Field("Connection", standing.name.lowercase().replaceFirstChar { it.uppercase() })
            Field("Paired", "Yes — token and certificate stored on this phone")

            Spacer(Modifier.height(12.dp))
            Text("Certificate", style = MaterialTheme.typography.bodyMedium)
            Text(
                text = Pinning.shortFingerprint(device.fingerprint),
                style = MaterialTheme.typography.titleMedium,
                fontFamily = FontFamily.Monospace,
            )
            Text(
                "This is the pin. Every connection to this PC is refused unless it presents " +
                    "exactly this certificate. It should match the Certificate line on the " +
                    "PC's My Device page.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = { onCopyFingerprint(device.fingerprint) }) {
                Text("Copy full fingerprint")
            }

            Spacer(Modifier.height(24.dp))
            HorizontalDivider()
            Spacer(Modifier.height(24.dp))

            Text("This phone", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(12.dp))
            Field("Name", phoneName)
            Field("Device id", phoneClientId)
            Text(
                "The PC lists this phone under that id. LanLink on the phone does not accept " +
                    "incoming connections, so it has no address or invite of its own — pairing " +
                    "always starts from the PC's invite.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Spacer(Modifier.height(24.dp))
            OutlinedButton(onClick = onForget, modifier = Modifier.fillMaxWidth()) {
                Text("Forget this device")
            }
        }
    }
}

@Composable
private fun Field(label: String, value: String) {
    Column(Modifier.padding(bottom = 10.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyLarge)
    }
}
