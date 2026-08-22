package link.lan.android.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import link.lan.app.PairingStep
import link.lan.android.vm.PairingUiState

/**
 * Where a device comes from: a scanned QR, a pasted invite, or an address.
 *
 * Nothing is judged here. The text goes to `examineInvite` in android/logic,
 * which decides whether it is an address at all, whether it is already paired,
 * and whether it asks for a connection LanLink refuses to make.
 */
@Composable
fun AddDeviceDialog(
    initialProblem: String? = null,
    onDismiss: () -> Unit,
    onSubmit: (String) -> String?,
    onScan: () -> Unit,
    onPaste: () -> String?,
) {
    var text by remember { mutableStateOf("") }
    var problem by remember { mutableStateOf(initialProblem) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add a device") },
        text = {
            Column {
                Text(
                    "On your PC open LanLink → My Device. Then either:",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "• Scan QR code — the camera reads the code on the PC screen\n" +
                        "• Paste invite — press Copy invite link on the PC\n" +
                        "• Type the address it shows, such as 192.168.1.4:8765",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                TextButton(onClick = { onPaste()?.let { text = it; problem = null } }) {
                    Text("Paste from clipboard")
                }
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it; problem = null },
                    label = { Text("lanlink:// invite or 192.168.1.4:8765") },
                    singleLine = true,
                    isError = problem != null,
                    modifier = Modifier.fillMaxWidth(),
                )
                if (problem != null) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        problem!!,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { problem = onSubmit(text) },
                enabled = text.isNotBlank(),
            ) { Text("Continue") }
        },
        dismissButton = {
            TextButton(onClick = onScan) { Text("Scan QR code") }
        },
    )
}

/**
 * The pairing conversation, in the order [link.lan.app.PairingFlow] enforces.
 *
 * The code field does not appear until the certificate has been confirmed.
 * That is a courtesy, not the safeguard: the flow refuses a code sent early
 * whatever this dialog does.
 */
@Composable
fun PairingDialog(
    state: PairingUiState,
    onConfirmFingerprint: (Boolean) -> Unit,
    onSubmitCode: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var code by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (state.step == PairingStep.PAIRED) "Paired" else "Pair with ${state.target}") },
        text = {
            Column {
                when {
                    state.working && state.presented == null ->
                        Text("Asking ${state.target} for its certificate…")

                    state.step == PairingStep.FAILED ->
                        Text(
                            state.problem ?: "Pairing did not complete.",
                            color = MaterialTheme.colorScheme.error,
                        )

                    state.step == PairingStep.PAIRED ->
                        Text("${state.paired?.name.orEmpty()} is paired. Its token and certificate are stored on this phone.")

                    state.needsHumanConfirmation -> {
                        Text(
                            "Check this matches the Certificate line on the PC, in " +
                                "LanLink → My Device:",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Spacer(Modifier.height(12.dp))
                        Text(
                            text = state.fingerprint,
                            style = MaterialTheme.typography.titleMedium,
                            fontFamily = FontFamily.Monospace,
                        )
                        Spacer(Modifier.height(12.dp))
                        Text(
                            "If it does not match, say no. Nothing has been sent to that " +
                                "device yet.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    state.awaitingCode -> {
                        Text(
                            "The certificate matches. Type the pairing code shown on the PC.",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Spacer(Modifier.height(12.dp))
                        OutlinedTextField(
                            value = code,
                            onValueChange = { code = it },
                            label = { Text("Pairing code") },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        if (state.problem != null) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                state.problem,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                    }

                    state.working -> Text("Talking to ${state.target}…")
                    else -> Text(state.problem ?: "Working…")
                }

                if (state.working) {
                    Spacer(Modifier.height(16.dp))
                    CircularProgressIndicator(Modifier.height(20.dp))
                }
            }
        },
        confirmButton = {
            when {
                state.step == PairingStep.PAIRED ->
                    TextButton(onClick = onDismiss) { Text("Done") }

                state.needsHumanConfirmation ->
                    TextButton(onClick = { onConfirmFingerprint(true) }) { Text("It matches") }

                state.awaitingCode ->
                    TextButton(
                        onClick = { onSubmitCode(code) },
                        enabled = code.isNotBlank() && !state.working,
                    ) { Text("Pair") }

                else -> TextButton(onClick = onDismiss) { Text("Close") }
            }
        },
        dismissButton = {
            if (state.needsHumanConfirmation) {
                TextButton(onClick = { onConfirmFingerprint(false) }) { Text("It does not") }
            } else if (state.step != PairingStep.PAIRED) {
                TextButton(onClick = onDismiss) { Text("Cancel") }
            }
        },
    )
}

/**
 * A paired device that has turned up somewhere else.
 *
 * Networks renumber — a PC that was on a hotspot yesterday is on the router
 * today — and mDNS cannot always say so, because plenty of networks block
 * multicast. This does not re-pair anything: `relocate` in android/logic
 * refuses any host whose certificate is not the one already stored, so the
 * token can follow the device but can never be aimed at a different machine.
 */
@Composable
fun UpdateAddressDialog(
    deviceName: String,
    currentAddress: String,
    onDismiss: () -> Unit,
    onSubmit: (String, Int) -> Unit,
) {
    var text by remember { mutableStateOf(currentAddress) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Where is $deviceName?") },
        text = {
            Column {
                Text(
                    "Type the address LanLink shows on that PC, under My Device. The " +
                        "certificate there must still be the one you paired with, or nothing " +
                        "will be changed.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    label = { Text("192.168.1.16:8765") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    val host = text.substringBefore(':').trim()
                    val port = text.substringAfter(':', "").trim().toIntOrNull()
                        ?: link.lan.core.DEFAULT_PORT
                    if (host.isNotEmpty()) onSubmit(host, port)
                },
                enabled = text.isNotBlank(),
            ) { Text("Look there") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
