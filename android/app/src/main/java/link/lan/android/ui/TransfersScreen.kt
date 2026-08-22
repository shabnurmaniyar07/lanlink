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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import link.lan.app.Direction
import link.lan.app.Transfer
import link.lan.app.TransferState
import link.lan.app.bytes
import link.lan.app.describeRemaining
import link.lan.app.describeSpeed
import link.lan.app.secondsRemaining

/**
 * The transfer list.
 *
 * One transfer runs at a time. That is the queue's decision, made in tested
 * logic for good reasons, and this screen shows it rather than arguing with it:
 * everything else says Waiting.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TransfersScreen(
    transfers: List<Transfer>,
    summary: String,
    speedOf: (Long) -> Long?,
    canOpen: (Long) -> Boolean,
    onBack: () -> Unit,
    onCancel: (Transfer) -> Unit,
    onRetry: (Transfer) -> Unit,
    onOpen: (Transfer) -> Unit,
    onClearFinished: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Transfers") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    if (transfers.any { it.isFinished }) {
                        TextButton(onClick = onClearFinished) { Text("Clear finished") }
                    }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            Text(
                text = summary,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(16.dp),
            )

            if (transfers.isEmpty()) {
                Text(
                    "Nothing has been transferred yet. Open a device, browse to a file and " +
                        "press Download, or use Upload files.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp),
                )
                return@Column
            }

            LazyColumn(Modifier.fillMaxSize()) {
                items(transfers, key = { it.id }) { item ->
                    TransferRow(
                        item = item,
                        speed = speedOf(item.id),
                        canOpen = canOpen(item.id),
                        onCancel = { onCancel(item) },
                        onRetry = { onRetry(item) },
                        onOpen = { onOpen(item) },
                    )
                }
            }
        }
    }
}

@Composable
private fun TransferRow(
    item: Transfer,
    speed: Long?,
    canOpen: Boolean,
    onCancel: () -> Unit,
    onRetry: () -> Unit,
    onOpen: () -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = if (item.direction == Direction.DOWNLOAD) "↓" else "↑",
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.size(10.dp))
            Column(Modifier.weight(1f)) {
                Text(item.name, style = MaterialTheme.typography.bodyLarge)
                Text(
                    text = "${item.deviceName} · ${label(item.state)}",
                    style = MaterialTheme.typography.bodySmall,
                    color = colourFor(item.state),
                )
            }
        }

        if (item.state == TransferState.RUNNING) {
            Spacer(Modifier.height(8.dp))
            val fraction = item.fraction
            if (fraction != null) {
                LinearProgressIndicator(progress = { fraction }, modifier = Modifier.fillMaxWidth())
            } else {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            }
            Spacer(Modifier.height(6.dp))
            Text(
                text = progressLine(item, speed),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else if (item.state == TransferState.FAILED) {
            Spacer(Modifier.height(4.dp))
            Text(
                text = item.problem.orEmpty(),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            when {
                item.isActive -> TextButton(onClick = onCancel) { Text("Cancel") }
                item.canRetry -> TextButton(onClick = onRetry) {
                    Text(if (item.canResume) "Resume" else "Retry")
                }

                item.state == TransferState.DONE && canOpen ->
                    TextButton(onClick = onOpen) { Text("Open") }
            }
        }
    }
}

private fun progressLine(item: Transfer, speed: Long?): String {
    val moved = bytes(item.transferred)
    val total = item.size?.let { " of ${bytes(it)}" }.orEmpty()
    val percent = item.fraction?.let { " · ${(it * 100).toInt()}%" }.orEmpty()
    val rate = describeSpeed(speed).let { if (it.isEmpty()) "" else " · $it" }
    val left = describeRemaining(secondsRemaining(item.size, item.transferred, speed))
        .let { if (it.isEmpty()) "" else " · $it" }
    return "$moved$total$percent$rate$left"
}

private fun label(state: TransferState): String = when (state) {
    TransferState.WAITING -> "Waiting"
    TransferState.RUNNING -> "Running"
    TransferState.DONE -> "Completed"
    TransferState.FAILED -> "Failed"
    TransferState.CANCELLED -> "Cancelled"
}

@Composable
private fun colourFor(state: TransferState): Color = when (state) {
    TransferState.DONE -> Color(0xFF2E7D32)
    TransferState.FAILED -> MaterialTheme.colorScheme.error
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}
