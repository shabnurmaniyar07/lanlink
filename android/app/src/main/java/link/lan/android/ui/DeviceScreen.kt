package link.lan.android.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Mouse
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import link.lan.android.vm.DeviceUiState
import link.lan.app.Standing

/**
 * One device: whether it is usable, and what can be done with it.
 *
 * Connecting is not a formality here. Nothing offers to browse or upload until
 * the certificate matched the stored pin *and* the token was accepted, and a
 * device presenting the wrong certificate is refused outright rather than
 * offered a retry.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DeviceScreen(
    state: DeviceUiState,
    onBack: () -> Unit,
    onBrowse: () -> Unit,
    onUpload: () -> Unit,
    onInformation: () -> Unit,
    onTrackpad: () -> Unit = {},
    onRetry: () -> Unit,
    onTransfers: () -> Unit,
) {
    val device = state.device

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(device?.name ?: "Device") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier.fillMaxSize().padding(padding).padding(20.dp),
        ) {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = when (state.standing) {
                        Standing.CONNECTED -> MaterialTheme.colorScheme.secondaryContainer
                        Standing.IMPOSTOR -> MaterialTheme.colorScheme.errorContainer
                        else -> MaterialTheme.colorScheme.surfaceVariant
                    },
                ),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = "Status: ${describe(state.standing)}",
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Spacer(Modifier.size(10.dp))
                        if (state.connecting) {
                            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                        }
                    }
                    Spacer(Modifier.height(6.dp))
                    Text(state.message, style = MaterialTheme.typography.bodyMedium)
                    device?.let {
                        Spacer(Modifier.height(6.dp))
                        Text(
                            it.address,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            Spacer(Modifier.height(24.dp))

            Button(
                onClick = onBrowse,
                enabled = state.connected,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Browse files") }

            Spacer(Modifier.height(10.dp))
            OutlinedButton(
                onClick = onUpload,
                enabled = state.connected,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Upload files") }

            Spacer(Modifier.height(10.dp))
            OutlinedButton(
                onClick = onTrackpad,
                enabled = state.connected,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Filled.Mouse, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Virtual Trackpad & Remote")
            }

            Spacer(Modifier.height(10.dp))
            OutlinedButton(onClick = onInformation, modifier = Modifier.fillMaxWidth()) {
                Text("Device information")
            }

            Spacer(Modifier.height(10.dp))
            OutlinedButton(onClick = onTransfers, modifier = Modifier.fillMaxWidth()) {
                Text("Transfers")
            }

            if (state.worthRetrying && !state.connecting) {
                Spacer(Modifier.height(24.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onRetry) { Text("Try again") }
                }
            }

            if (state.isImpostor) {
                Spacer(Modifier.height(24.dp))
                Text(
                    "LanLink will not connect to this device while it presents a different " +
                        "certificate. Nothing was sent to it. If you reinstalled LanLink on " +
                        "that PC, forget the device here and pair again.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

private fun describe(standing: Standing): String = when (standing) {
    Standing.CONNECTED -> "Connected"
    Standing.OFFLINE -> "Offline"
    Standing.IMPOSTOR -> "Refused — certificate changed"
    Standing.UNAUTHORISED -> "Not authorised"
    Standing.PROBLEM -> "Problem"
}
